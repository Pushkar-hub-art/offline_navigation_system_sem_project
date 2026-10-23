import pytest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL = "http://127.0.0.1:5000"
START = "18.5204, 73.8567"   # replace with points inside YOUR map
END = "18.5300, 73.8500"

@pytest.fixture
def driver():
    d = webdriver.Chrome()
    d.get(URL)
    yield d
    d.quit()

def fill(d, start, end):
    d.find_element(By.ID, "start-input").send_keys(start)
    d.find_element(By.ID, "end-input").send_keys(end)

def test_valid_route(driver):
    fill(driver, START, END)
    driver.find_element(By.ID, "btn-route").click()
    WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.ID, "result")))
    assert driver.find_element(By.ID, "sum-distance").text != "–"
    driver.save_screenshot("tc1_valid_route.png")

def test_swap(driver):
    fill(driver, START, END)
    driver.find_element(By.ID, "btn-swap").click()
    assert driver.find_element(By.ID, "start-input").get_attribute("value") == END

def test_clear(driver):
    fill(driver, START, END)
    driver.find_element(By.ID, "btn-route").click()
    WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.ID, "result")))
    driver.find_element(By.ID, "btn-clear").click()
    assert driver.find_element(By.ID, "start-input").get_attribute("value") == ""
    assert not driver.find_element(By.ID, "result").is_displayed()

def test_same_point_shows_error(driver):
    fill(driver, START, START)
    driver.find_element(By.ID, "btn-route").click()
    WebDriverWait(driver, 10).until(
        lambda d: d.find_element(By.ID, "status").text != "")