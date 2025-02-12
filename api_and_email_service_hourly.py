import logging
from datetime import timedelta, datetime
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import requests
import json
import pytz
import pymysql
import keyring
import pandas as pd
from sqlalchemy import create_engine, text
import smtplib
from timezonefinder import TimezoneFinder
from sqlalchemy.sql import text  # Import SQL text wrapper
import sqlalchemy  # Import SQLAlchemy for exception handling

from initialize_mysql import *

logging.basicConfig(
    filename="/logs/api_and_email_service_hourly.log",
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt=f"%Y-%m-%d %H:%M:%S ({pytz.timezone('US/Pacific')})",
)

# Configure timezone
tz = pytz.timezone("US/Pacific")
logging.Formatter.converter = lambda *args: datetime.now(tz).timetuple()

# Run API and email at 8 AM local hour
LOCAL_TIME_HOUR = 8

OPENWEATHERMAP_AUTH_api_key = keyring.get_password("OPENWEATHERMAP_AUTH", "api_key")


class ApiAndEmailServiceHourly:
    def __init__(self, engine):
        self.engine = engine

    def run_query(self, query):
        try:
            with self.engine.connect() as connection:
                connection.execute(text(query))  # Wrap the query string with `text`
        except sqlalchemy.exc.IntegrityError as e:
            print(f"IntegrityError: {e}")
        except sqlalchemy.exc.ObjectNotExecutableError as e:
            print(f"ObjectNotExecutableError: {e}")
        except Exception as e:
            print(f"Error running query: {e}")

    def fetch(self, url):
        response = requests.get(url)
        return response.text

    def api_and_email_task(self, cityID, city_name, recipients, local_tz):
        _local_tz = pytz.timezone(local_tz)
        local_time = datetime.now(_local_tz)
        local_dateFact = local_time.strftime("%Y-%m-%d")
        local_tomorrow = (local_time + timedelta(days=1)).strftime("%Y-%m-%d")
        local_hour = local_time.hour + 1

        logging.info(f"Starting `api_and_email_task` for {city_name} at {local_time}")

        delete_query = f"""DELETE from klaviyo.tblDimEmailCity
            where
              DATE_SUB(
                STR_TO_DATE(SUBSTR(sign_up_date, 1, 10), '%Y-%m-%d'),
                INTERVAL -10 DAY
              ) <= CURRENT_DATE;  """

        query_output = "SELECT * FROM klaviyo.tblDimEmailCity"
        query_df = pd.read_sql_query(
            query_output,
            con=self.engine,
        )
        logging.info("BEFORE DELETE")
        logging.info(query_df.to_string())

        # delete operation
        self.run_query(delete_query)

        query_df = pd.read_sql_query(
            query_output,
            con=self.engine,
        )
        logging.info("AFTER DELETE")
        logging.info(query_df.to_string())

        
        logging.info(
            f"Skipping `api_and_email_task` for {city_name} since local hour {local_hour} != {LOCAL_TIME_HOUR}"
        )


        # call current weather api for city
        url = f"http://api.openweathermap.org/data/2.5/weather?id={cityID}&appid={OPENWEATHERMAP_AUTH_api_key}"
        curr_r = self.fetch(url)
        curr_obj = json.loads(curr_r)

        today_weather = "-".join(
            [curr_obj["weather"][0]["main"], curr_obj["weather"][0]["description"]]
        )
        today_max_degrees_F = int(
            (float(curr_obj["main"]["temp_max"]) * (9 / 5)) - 459.67
        )
        logging.info(
            f"{city_name}: today_weather = {today_weather}; {city_name} today_max_degrees_F = {today_max_degrees_F}"
        )

        # call forecast weather api for city
        url = f"http://api.openweathermap.org/data/2.5/forecast?id={cityID}&appid={OPENWEATHERMAP_AUTH_api_key}"
        forecast_r = self.fetch(url)
        forecast_obj = json.loads(forecast_r)

        tmrw_objs = [
            x for x in forecast_obj["list"] if x["dt_txt"][0:10] == local_tomorrow
        ]
        tomorrow_max_degrees_F = int(
            (
                float(max([tmrw_obj["main"]["temp_max"] for tmrw_obj in tmrw_objs]))
                * (9 / 5)
            )
            - 459.67
        )
        logging.info(f"{city_name}: tomorrow_max_degrees_F = {tomorrow_max_degrees_F}")

        query = f"from klaviyo.tblFactCityWeather where dateFact='{local_dateFact}' and city_id={cityID} "
        delete_query = "DELETE " + query
        query_output = "SELECT * " + query

        query_df = pd.read_sql_query(
            query_output,
            con=self.engine,
        )
        logging.info("BEFORE DELETE")
        logging.info(query_df.to_string())

        # delete operation
        self.run_query("DELETE " + query)

        query_df = pd.read_sql_query(
            query_output,
            con=self.engine,
        )
        logging.info("AFTER DELETE")
        logging.info(query_df.to_string())

        query = f"""
        INSERT INTO klaviyo.tblFactCityWeather (city_id, dateFact, today_weather, today_max_degrees_F, tomorrow_max_degrees_F)
        VALUES ({cityID}, '{local_dateFact}', '{today_weather}', {today_max_degrees_F}, {tomorrow_max_degrees_F})
        ON DUPLICATE KEY UPDATE
        today_weather = VALUES(today_weather),
        today_max_degrees_F = VALUES(today_max_degrees_F),
        tomorrow_max_degrees_F = VALUES(tomorrow_max_degrees_F);
        """
        self.run_query(query)

        logging.info(
            f"successfully finished INSERT INTO klaviyo.tblFactCityWeather({str(cityID)}, {str(local_dateFact)}, {str(today_weather)}, {str(today_max_degrees_F)}, {str(tomorrow_max_degrees_F)})"
        )

        precipitation_words = ["mist", "rain", "sleet", "snow", "hail"]
        sunny_words = ["sunny", "clear"]
        if any(x in today_weather for x in sunny_words):
            logging.info("sunny")
            subject_value = f"WEATHER APP: It's nice out in {city_name}!"
            gif_link = "https://media.giphy.com/media/nYiHd4Mh3w6fS/giphy.gif"
        elif today_max_degrees_F >= tomorrow_max_degrees_F + 5:
            logging.info("warm")
            subject_value = f"WEATHER APP: It's nice out in {city_name}!"
            gif_link = "https://media.giphy.com/media/nYiHd4Mh3w6fS/giphy.gif"
        elif any(x in today_weather for x in precipitation_words):
            logging.info("precipitation")
            subject_value = f"WEATHER APP: Not so nice out in {city_name}? That's okay."
            gif_link = "https://media.giphy.com/media/1hM7Uh46ixnsWRMA7w/giphy.gif"
        elif today_max_degrees_F + 5 <= tomorrow_max_degrees_F:
            logging.info("cold")
            subject_value = f"WEATHER APP: Not so nice out in {city_name}? That's okay."
            gif_link = "https://media.giphy.com/media/26FLdaDQ5f72FPbEI/giphy.gif"
        else:
            logging.info("other")
            subject_value = f"WEATHER APP: {city_name}"
            gif_link = "https://media.giphy.com/media/3o6vXNLzXdW4sbFRGo/giphy.gif"

        for recipient in recipients:
            logging.info(f'starting {recipient} ')
            expiration_df = pd.read_sql_query(
                f"SELECT DATE_ADD(sign_up_date, INTERVAL 10 DAY) AS expiration_date from klaviyo.tblDimEmailCity where city_id={cityID} AND email='{recipient}' LIMIT 1",
                con=self.engine,
            )

            for row in expiration_df.itertuples(index=True, name="Pandas"):
                expiration_datetime = getattr(row, "expiration_date")
                expiration_date = str(expiration_datetime)[0:10]

            message = MIMEMultipart()
            message["From"] = GMAIL_AUTH["mail_username"]
            message["To"] = recipient
            message["Subject"] = Header(subject_value, "utf-8")

            message.attach(
                MIMEText(
                    f"{city_name} - {today_max_degrees_F} degrees F - {today_weather} <br><br><img src='{gif_link}' width='300' height='300'> <br><br> expires {expiration_date}",
                    "html",
                )
            )

            with smtplib.SMTP(
                keyring.get_password("GMAIL_AUTH", "mail_server"), 587
            ) as server:
                server.starttls()
                server.login(
                    keyring.get_password("GMAIL_AUTH", "mail_username"),
                    keyring.get_password("GMAIL_AUTH", "mail_password"),
                )
                server.send_message(message)
                logging.info(f"Sent email to {recipient}")

            # purge data test
            query = f"from klaviyo.tblDimEmailCity where SUBSTR(sign_up_date,1,10) <= '{expiration_date}' and email='{recipient}' "
            delete_query = "DELETE " + query
            query_output = "SELECT * " + query
            query_df = pd.read_sql_query(
                query_output,
                con=self.engine,
            )
            logging.info("BEFORE DELETE")
            logging.info(query_df.to_string())

            # delete operation
            self.run_query("DELETE " + query)

            query_df = pd.read_sql_query(
                query_output,
                con=self.engine,
            )
            logging.info("AFTER DELETE")
            logging.info(query_df.to_string())
            logging.info(f'ending {recipient} ')
        return logging.info(f"finished function `api_and_email_task` for {city_name}")

    def main(self):
        dateFact = datetime.now(tz).strftime("%Y-%m-%d")
        dateFact_hour = datetime.now(tz).hour + 1
        logging.info(
            "------------------------------------------------------------------------"
        )
        logging.info(
            "------------------------------------------------------------------------"
        )
        logging.info(
            f"Starting api_and_email_service_hourly.py for {dateFact} and hour = {str(dateFact_hour)}"
        )

        tblDimEmailCity = pd.read_sql_query(
            """SELECT group_concat(convert(email,char)) AS email_set, city_id FROM klaviyo.tblDimEmailCity group by city_id""",
            con=engine,
        )
        logging.info("tblDimEmailCity")
        logging.info(tblDimEmailCity.to_string())

        tf = TimezoneFinder()
        _tz = []
        _utc_offset_seconds = []

        for row in tblDimEmailCity.itertuples(index=True, name="Pandas"):
            cityID = getattr(row, "city_id")
            city_name = city_dict[str(cityID)]
            lat = city_dict_nested[cityID]["lat"]
            lon = city_dict_nested[cityID]["lon"]
            try:
                _tz_value = tf.timezone_at(lng=lon, lat=lat)
                if _tz_value is None:
                    logging.error(f"No timezone found for {lat} {lon}")
                    continue
                _tz.append(_tz_value)
                logging.info(f"found {_tz_value} as timezone for {lat} {lon}")
                try:
                    # Get the current UTC time
                    _now_utc = datetime.now(pytz.UTC)
                    now_utc = _now_utc.replace(tzinfo=None)

                    # Create the timezone
                    _timezone = pytz.timezone(_tz_value)

                    # Localize the time
                    _localized_time = _timezone.localize(now_utc)

                    # Get the UTC offset in seconds
                    _utc_offset_seconds_value = (
                        _localized_time.utcoffset().total_seconds()
                    )
                    _utc_offset_seconds.append(_utc_offset_seconds_value)
                except pytz.UnknownTimeZoneError:
                    logging.error(f"Unknown timezone {_tz_value} for {lat} {lon}")
                except Exception as e:
                    logging.error(
                        f"utc offset seconds calculation error for {lat} {lon} at timezone {_tz_value}. Error: {str(e)}"
                    )
            except Exception as e:
                logging.error(f"TimezoneFinder error for {lat} {lon}. Error: {str(e)}")

        tblDimEmailCity["tz"] = _tz
        tblDimEmailCity["utc_offset_seconds"] = _utc_offset_seconds
        tblDimEmailCity_sorted = tblDimEmailCity.sort_values("utc_offset_seconds")

        for row in tblDimEmailCity_sorted.itertuples(index=True, name="Pandas"):
            recipients = str(getattr(row, "email_set")).split(",")
            cityID = getattr(row, "city_id")
            local_tz = getattr(row, "tz")
            utc_offset_seconds = getattr(row, "utc_offset_seconds")
            city_name = city_dict[str(cityID)]
            logging.info(" ")
            logging.info(
                f"cityID={str(cityID)}, city_name={city_name}, local_tz={local_tz}, utc_offset_hours={round(utc_offset_seconds/3600.,1)}"
            )

            self.api_and_email_task(cityID, city_name, recipients, local_tz)
            logging.info(" ")
        logging.info(
            f"Finished api_and_email_service_hourly.py for {dateFact} and hour = {str(dateFact_hour)}"
        )
        logging.info(
            "------------------------------------------------------------------------"
        )
        logging.info(
            "------------------------------------------------------------------------"
        )


if __name__ == "__main__":
    engine = create_engine(
        "mysql+pymysql://%s:%s@%s/klaviyo"
        % (
            keyring.get_password("MYSQL_AUTH", "user"),
            keyring.get_password("MYSQL_AUTH", "password"),
            keyring.get_password("MYSQL_AUTH", "host"),
        )
    )
    weather_api = ApiAndEmailServiceHourly(engine)
    weather_api.main()
