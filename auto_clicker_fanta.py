from selenium import webdriver
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys



# Create the webdriver object. Here the
# chromedriver is present in the driver
# folder of the root directory.
driver = webdriver.Chrome()

#insert here the url of your league from fantacalcio
driver.get("league url")

button1 =driver.find_element(By.XPATH,"/html/body/nav[2]/div/a[2]")

driver.execute_script("arguments[0].scrollIntoView();", button1)
driver.execute_script("arguments[0].click();",button1 )

time.sleep(3)
# Locate and fill the username field
username_input = driver.find_element(By.XPATH, "/html/body/app-root/layout-auth/div[1]/div/view-login/nz-card/div[2]/form/nz-form-item[1]/nz-form-control/div/div/nz-input-group/input")
driver.execute_script("arguments[0].scrollIntoView();", username_input)
#insert here your email
username_input.send_keys('mail')

# Locate and fill the password field
password_input = driver.find_element(By.XPATH, "/html/body/app-root/layout-auth/div[1]/div/view-login/nz-card/div[2]/form/nz-form-item[2]/nz-form-control/div/div/nz-input-group/input")
driver.execute_script("arguments[0].scrollIntoView();", password_input)
#insert here your password
password_input.send_keys('password')

# Submit the form
password_input.send_keys(Keys.RETURN)

# Wait for login to process
time.sleep(2)

driver.get("https://leghe.fantacalcio.it/rusticatorsapienza/area-gioco/inserisci-formazione?id=362238")

time.sleep(2)

button2=driver.find_element(By.XPATH,'//*[@id="formation"]/div[2]/div[5]/button[1]')

driver.execute_script("arguments[0].scrollIntoView();", button2)
driver.execute_script("arguments[0].click();",button2 )


time.sleep(2)

driver.get("https://leghe.fantacalcio.it/ilprimoverofanta/area-gioco/inserisci-formazione?id=416045")

time.sleep(2)

button3=driver.find_element(By.XPATH,'//*[@id="formation"]/div[2]/div[5]/button[1]')

driver.execute_script("arguments[0].scrollIntoView();", button3)
driver.execute_script("arguments[0].click();",button3 )

time.sleep(3)

