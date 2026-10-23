import os
import time
from random import randint
from shutil import which

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class FeedbackUnavailableError(RuntimeError):
    pass


def create_driver():
    options = webdriver.ChromeOptions()
    chrome_binary = os.environ.get("FEEDBACK_CHROME_BINARY") or (
        which("chromium")
        or which("chromium-browser")
        or which("google-chrome")
        or which("chrome")
    )
    if chrome_binary:
        options.binary_location = chrome_binary
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")

    driver_path = os.environ.get("FEEDBACK_CHROMEDRIVER") or which("chromedriver")
    if driver_path:
        return webdriver.Chrome(service=Service(driver_path), options=options)
    return webdriver.Chrome(options=options)


def _intermediate_form(browser):
    wait = WebDriverWait(browser, 10)
    courses = browser.find_elements(By.CLASS_NAME, "intermediate-body")
    if not courses:
        raise FeedbackUnavailableError("No intermediate feedback form was found.")

    for course_index in range(len(courses)):
        courses = browser.find_elements(By.CLASS_NAME, "intermediate-body")
        browser.execute_script(
            "arguments[0].scrollIntoView(); arguments[0].click();",
            courses[course_index],
        )
        questions_text = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "div.bottom-0"))
        ).text
        questions = int(questions_text.split()[-1])

        for question_index in range(questions):
            radio_button = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, f"//label[@for='radio-{question_index + 1}-1']")
                )
            )
            browser.execute_script("arguments[0].click();", radio_button)
            next_button = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[@class='carousel-control-next']")
                )
            )
            browser.execute_script("arguments[0].click();", next_button)

        back = browser.find_element(By.CLASS_NAME, "overlay")
        browser.execute_script("arguments[0].click();", back)


def _endsem_form(browser):
    wait = WebDriverWait(browser, 10)
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.staff-item")))
    staff_list = browser.find_elements(By.CSS_SELECTOR, "div.staff-item")

    for staff_index in range(len(staff_list)):
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.staff-item")))
        staff_list = browser.find_elements(By.CSS_SELECTOR, "div.staff-item")
        browser.execute_script(
            "arguments[0].scrollIntoView();arguments[0].click()",
            staff_list[staff_index],
        )
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "span.ms-1")))
        wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//tbody[@id='feedbackTableBody']/tr[1]/td[@class='rating-cell']/div[@class='star-rating']/label[1]",
                )
            )
        )

        review_list = browser.find_elements(By.CSS_SELECTOR, "td.question-cell")
        for question_index in range(1, len(review_list) + 1):
            star_button = browser.find_element(
                By.XPATH,
                f"//tbody[@id='feedbackTableBody']/tr[{question_index}]/td[@class='rating-cell']/div[@class='star-rating']/label[{randint(1, 2)}]",
            )
            browser.execute_script(
                "arguments[0].scrollIntoView();arguments[0].click()", star_button
            )

        submit_button = browser.find_element(By.ID, "btnSave")
        browser.execute_script(
            "arguments[0].scrollIntoView();arguments[0].click()", submit_button
        )
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "img.img-fluid")))
        time.sleep(1)

    final_submit_button = browser.find_element(By.ID, "btnFinalSubmit")
    browser.execute_script(
        "arguments[0].scrollIntoView();arguments[0].click()", final_submit_button
    )


def run_feedback(feedback_type: int, roll_number: str, password: str) -> None:
    if feedback_type not in (0, 1):
        raise ValueError("feedback_type must be 0 (end-semester) or 1 (intermediate).")

    browser = create_driver()
    try:
        wait = WebDriverWait(browser, 10)
        browser.get("https://ecampus.psgtech.ac.in/studzone")

        roll_number_field = wait.until(
            EC.presence_of_element_located((By.ID, "rollno"))
        )
        roll_number_field.send_keys(roll_number)
        browser.find_element(By.ID, "password").send_keys(password)
        terms_checkbox = browser.find_element(By.ID, "terms")
        browser.execute_script("arguments[0].click();", terms_checkbox)
        login_button = browser.find_element(By.ID, "btnLogin")
        browser.execute_script("arguments[0].click();", login_button)

        feedback_card = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//h5[text()='Feedback']"))
        )
        browser.execute_script(
            "arguments[0].scrollIntoView();arguments[0].click();", feedback_card
        )
        try:
            wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "card-body")))
        except TimeoutException as error:
            if feedback_type == 1:
                raise FeedbackUnavailableError(
                    "No intermediate feedback form was found."
                ) from error
            raise
        feedbacks = browser.find_elements(By.CLASS_NAME, "card-body")
        if feedback_type == 1 and len(feedbacks) <= feedback_type:
            raise FeedbackUnavailableError("No intermediate feedback form was found.")
        browser.execute_script("arguments[0].click();", feedbacks[feedback_type])

        if feedback_type == 0:
            _endsem_form(browser)
        else:
            _intermediate_form(browser)
    finally:
        browser.quit()
