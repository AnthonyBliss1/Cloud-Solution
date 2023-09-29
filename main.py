from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import TimeoutException
import datetime
import pytz
import requests
import mysql.connector
import boto3 
import os
import json
from botocore.exceptions import ClientError


def main(event, context):
    options = Options()
    options.binary_location = '/opt/headless-chromium'
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--single-process')
    options.add_argument('--disable-dev-shm-usage')

    driver = webdriver.Chrome('/opt/chromedriver',chrome_options=options)

    # Get environment variables
    LOGIN_URL = os.getenv('LOGIN_URL')
    account_info = os.getenv('account_info')
    username_info = os.getenv('username_info')
    password_info = os.getenv('password_info')
    reading_page = os.getenv('reading_page')
    zipcode = os.getenv('zipcode')
    API_KEY = os.getenv('API_Key')
    T1_path = os.getenv('T1_xpath')
    T2_path = os.getenv('T2_xpath')
    T3_path = os.getenv('T3_xpath')
    profile_icon_path = os.getenv('profile_icon_xpath')
    menu_icon_path = os.getenv('menu_icon_xpath')
    
    # Go to login page
    driver.get(LOGIN_URL)

    # Initialize wait 
    wait = WebDriverWait(driver, 30)

    # Wait for first field to be present then fill out all fields and login
    account_field = wait.until(EC.presence_of_element_located((By.NAME, "account")))
    account_field.send_keys(account_info)
    username_field = driver.find_element(By.NAME, "username")
    username_field.send_keys(username_info)
    password_field = driver.find_element(By.NAME, "password")
    password_field.send_keys(password_info)
    password_field.send_keys(Keys.RETURN)

    # Navigate to screen where trailer readings are posted
    next_screen_button = wait.until(EC.element_to_be_clickable((By.LINK_TEXT, reading_page)))
    next_screen_button.click()

    # Get current date and time in US eastern time
    eastern = pytz.timezone('US/Eastern')
    current_datetime = datetime.datetime.now(eastern)
    current_date = current_datetime.strftime('%Y-%m-%d')
    current_time = current_datetime.strftime('%H:%M:%S')

    # OpenWeatherMap API to get the temperature
    ZIP_CODE = zipcode
    COUNTRY_CODE = 'us'
    URL = f"http://api.openweathermap.org/data/2.5/weather?zip={ZIP_CODE},{COUNTRY_CODE}&appid={API_KEY}&units=imperial"
    response = requests.get(URL)
    weather_data = response.json()
    temperature = weather_data['main']['temp']

   # Initialize data dictionary 
    data_dict = {
        "Date": current_date,
        "Time": current_time,
        "Trailer_1_Pressure": '0',
        "Trailer_2_Pressure": '0',
        "Trailer_3_Pressure": '0',
        "Temperature": temperature,
        "Offline": True
    }

    # List of xpaths for trailer readings
    xpaths = {
        'Trailer_1_Pressure': T1_path,
        'Trailer_2_Pressure': T2_path,
        'Trailer_3_Pressure': T3_path
    }

    # Loop through xpaths and extract the corresponding pressure readings, update dictionary
    for key, xpath in xpaths.items():
        try:
            element = WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.XPATH, xpath)))
            data_dict[key] = element.text
            data_dict['Offline'] = False
        except TimeoutException:
            print(f"{key} not found, setting to default value.")

    def find_element_or_none(driver, by, value):
        try:
            return driver.find_element(by, value)
        except NoSuchElementException:
            return None

    # Check for menu icon
    menu_icon = find_element_or_none(driver, By.XPATH, menu_icon_path)
    
    # If menu icon is found, click it to reveal profile icon
    if menu_icon:
        menu_icon.click()
        profile_icon = wait.until(EC.element_to_be_clickable((By.XPATH, profile_icon_path)))

    # If profile icon is found, interact with it 
    if profile_icon:
        profile_icon.click()
    else:
        account_icon = wait.until(EC.element_to_be_clickable((By.ID, "top-profile-icon")))
        account_icon.click()

    # Logout of site
    logout_option = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Logout']")))
    logout_option.click()

    # Close driver
    driver.close()
    driver.quit()

    # Get secret from AWS Secret Manager
    def get_secret():
        secret_name = "MySQL/database"
        region_name = "us-east-1"
        # Create a Secrets Manager client
        session = boto3.session.Session()
        client = session.client(
            service_name='secretsmanager',
            region_name=region_name
        )
        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=secret_name
            )
        except ClientError as e:
            # For a list of exceptions thrown, see
            # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
            raise e
        # Decrypts secret using the associated KMS key.
        secret = get_secret_value_response['SecretString']
        return json.loads(secret)

    secret_dict = get_secret() 

    # Connect to database
    connection = mysql.connector.connect(
        host = secret_dict.get('host'),
        port = secret_dict.get('port'),
        database = secret_dict.get('db'),
        username = secret_dict.get('username'),
        password = secret_dict.get('password'),
        use_pure=True
    )
    cursor = connection.cursor()

    # SQL statement to insert data
    sql_insert_query = """
        INSERT INTO trailer_data (
            Date, 
            Time, 
            Trailer_1_Pressure, 
            Trailer_2_Pressure, 
            Trailer_3_Pressure, 
            Offline, 
            Temperature
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    data_tuple = (
        data_dict['Date'],
        data_dict['Time'],
        data_dict['Trailer_1_Pressure'],
        data_dict['Trailer_2_Pressure'],
        data_dict['Trailer_3_Pressure'],
        data_dict['Offline'],
        data_dict['Temperature']
    )
    cursor.execute(sql_insert_query, data_tuple)
    
    #Commit changes
    connection.commit()
    cursor.close()
    connection.close()

    print("Data inserted into AWS RDS")
