import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
import re
import requests

# # Options for the browser
# option = webdriver.ChromeOptions()
# option.add_argument('--headless')
#
# # Get and create the browser object
# service = Service(r'\web_driver\chromedriver.exe')
# browser = webdriver.Chrome(service=service, options=option)
#
# browser.get("https://www.airbnb.com/experiences/272085")  # navigate to URL
# # retrieve fully rendered HTML content
# content = browser.page_source
# browser.close()
#
# # we then could parse it with beautifulsoup
# soup = BeautifulSoup(content, "html.parser")
#
# links = []
# images = []
#
# # Find the urls in page
# for link in soup.select('a'):
#     found_link = link.get('href')
#     clean_to = re.sub(r"(\?).*$", "", found_link)
#     links.append(clean_to)
#
# # Find the images in page (<img> tags)
# for img in soup.select('img'):
#     found_src = img.get('src')
#     if found_src is not None:
#         images.append(found_src)
#
# print(links)
# print(images)
#
# time.sleep(5)
#
# links2 = []
# images2 = []
#
# response = requests.get("https://www.airbnb.com/experiences/272085", stream=True)
# html = response.text
#
# soup2 = BeautifulSoup(html, "html.parser")
#
# # Find the urls in page
# for link in soup2.select('a'):
#     found_link = link.get('href')
#     clean_to = re.sub(r"(\?).*$", "", found_link)
#     links2.append(clean_to)
#
# # Find the images in page (<img> tags)
# for img in soup2.select('img'):
#     found_src = img.get('src')
#     if found_src is not None:
#         images2.append(found_src)
#
# print(links2)
# print(images2)


# Testing finding links in onclick events
# Find the tags with onclick attribute using BeautifulSoup
response = requests.get("https://www.plus2net.com/html_tutorial/button-linking.php", stream=True)
soup = BeautifulSoup(response.text, "html.parser")

for tag in soup.find_all(onclick=True):
    found_link = tag.get('onclick')
    if found_link is not None:
        # Find the url in the onclick attribute
        valid_link = re.search(r"(?<=\').*(?=\')", found_link)
        if valid_link is not None:
            print(f"In {found_link} found {valid_link.group(0)}")
