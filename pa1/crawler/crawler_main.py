import datetime
import re
import socket
import time
import os
import threading
import requests
import urllib3
import hashlib
from queue import Queue
import logging
import dill
import keyboard
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from backend.sql_commands import DBManager

urllib3.disable_warnings()

# GLOBALS
USERAGENT = "fri-wier-threadripercki"
headers = {'User-Agent': USERAGENT}

frontier = Queue()      # urls to be visited
crawled_urls = set()    # urls that have been visited
domain_rules = {}       # robots.txt rules per visited domain
domain_ips = {}         # domain to ip address map
ip_last_visits = {}     # time of last visit per ip (to restrict request rate)
selenium_count = 0
bad_response_count = 0

# list of domains we want to visit
visit_domains = ["https://gov.si", "https://evem.gov.si", "https://e-uprava.gov.si", "https://e-prostor.gov.si"]

# Options for the selenium browser
option = webdriver.ChromeOptions()
option.add_argument('--headless')

# Logger setup
crawl_logger = logging.getLogger('crawler_logger')

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
file_handler = logging.FileHandler(f'info_log_{datetime.datetime.now():%Y-%m-%d_%H-%M}.log')
file_handler.setLevel(logging.WARNING)
error_handler = logging.FileHandler(f'error_log_{datetime.datetime.now():%Y-%m-%d_%H-%M}.log')
error_handler.setLevel(logging.ERROR)

formatter1 = logging.Formatter('%(asctime)s - %(message)s')
stream_handler.setFormatter(formatter1)
file_handler.setFormatter(formatter1)
error_handler.setFormatter(formatter1)

crawl_logger.addHandler(stream_handler)
crawl_logger.addHandler(file_handler)
crawl_logger.addHandler(error_handler)


def format_page_data(header):
    if "pdf" in header:
        return "PDF"
    elif "doc" in header:
        return "DOC"
    elif "docx" in header:
        return "DOCX"
    elif "ppt" in header:
        return "PPT"
    elif "pptx" in header:
        return "PPTX"
    else:
        # Regex to extract just the file type from Content-Type header
        if header is None or header == "":
            return ""
        if re.search(r"/(.*);?", header) is None:
            return ""
        data_type = re.search(r"/(.*);?", header).group(1).upper()
        return data_type[:20]


def add_to_crawled_urls(url):
    url = re.sub(r"/*([?#].*)?$", "", url)
    crawled_urls.add(url)


def get_hash(page_content):
    """Returns hash from the given HTML content."""
    encoded_content = page_content.encode('utf-8')
    hashcode = hashlib.sha256(encoded_content).hexdigest()
    return hashcode


def check_duplicate(conn, page_content, url):
    """Check if page is a duplicate of another."""
    if page_content is None:
        return False
    page_hash = get_hash(page_content)
    t = DBManager.check_if_page_exists(conn, page_hash, url)
    return t


def get_url_from_frontier():
    url = frontier.get()
    return url


def add_urls_to_frontier(links):
    for link in links:
        add_to_frontier(link['to_page'])


def add_to_frontier(url):
    # Removes query elements and anchor elements from url
    clean_url = re.sub(r"/*([?#].*)?$", "", url)
    if clean_url not in crawled_urls and clean_url not in frontier.queue:
        # Checks if url contains gov.si
        if "gov.si" in url:
            frontier.put(clean_url)
            return True
    return False


def request_page(url, web_driver=None, threadID=0):
    """Fetches page at url and returns HTML content and metadata as dict."""

    domain = urlparse(url).netloc
    site_data = None    # Parsed SITE metadata
    page_raw = {
        "html_content": None,
        "hashcode": None,
        "page_type_code": "HTML",
        "domain": domain,
        "url": url,
        "http_status_code": 0,
        "accessed_time": 0,
        "page_data": {},            # if page is BINARY, page_data contains metadata
        "duplicate_url": "",        # If page is duplicate, url of original page
    }
    global selenium_count
    global bad_response_count

    # If not enough time has elapsed since last request, return url to end of queue
    if domain in domain_ips:
        ip = domain_ips[domain]
        since_last_req = time.perf_counter() - ip_last_visits[ip]
        robots_delay = domain_rules[domain].crawl_delay(USERAGENT)
        min_delay = robots_delay if robots_delay is not None else 5
        if since_last_req < min_delay:
            add_to_frontier(url)
            return None, None

    else:
        # Save robots.txt rules for new domain
        robots_response = ""
        rp = RobotFileParser()
        robots_url = urljoin(domain, "robots.txt")
        robots_error = False
        try:
            robots_response = requests.get(robots_url, headers, verify=False, stream=True)
            rp.parse(robots_response.text.splitlines())
            domain_rules[domain] = rp
        except Exception as er:
            crawl_logger.error(f"Thread {threadID} Error fetching robots.txt: {url.encode('utf-8')}")
            crawl_logger.exception(er)
            add_to_crawled_urls(url)
            robots_error = True

        # Make site info dict with robots.txt and sitemap contents
        sitemap_urls = rp.site_maps()
        if sitemap_urls is None:
            sitemap_content = None
        else:
            sitemap_content = requests.get(sitemap_urls[0], headers).text
        site_data = {
            "domain": domain,
            "robots": robots_response.text if not robots_error else "",
            "sitemap": sitemap_content
        }

        if robots_error:
            bad_response_count += 1
            crawl_logger.warning(f"Thread {threadID} Response not ok, count: {bad_response_count}")

            # If response not ok, we must store a page_raw with only url and http_status_code
            page_raw["http_status_code"] = 400
            page_raw["page_type_code"] = "HTML"
            return page_raw, site_data

    # If URL is disallowed by robots.txt, don't fetch
    if not domain_rules[domain].can_fetch(USERAGENT, url):
        return None, site_data

    # Make a GET request
    crawl_logger.info(f"Thread {threadID} Fetching {url}")
    req_time = time.time()

    try:
        response = requests.get(url, headers, stream=True, verify=False)
    except Exception as er:
        crawl_logger.exception(f"Thread {threadID} Error fetching page: {url}")
        crawl_logger.exception(er)
        add_to_crawled_urls(url)
        return None, site_data

    # Save server IP and request time
    try:
        if socket.gethostbyname(domain):
            ip = socket.gethostbyname(domain)
            if domain not in domain_ips:
                domain_ips[domain] = ip
            ip_last_visits[ip] = req_time
    except Exception as er:
        crawl_logger.exception(er)
        pass

    page_raw["accessed_time"] = req_time
    page_raw["http_status_code"] = response.status_code

    # Check if we got redirected and if we already crawled the redirect url
    if response.history and re.sub(r"/*([?#].*)?$", "", response.url) != url:
        # If we got redirected and url was already crawled, mark page as duplicate
        crawl_logger.info(f"Thread {threadID} Already crawled redirect url {response.url}")
        page_raw["page_type_code"] = "DUPLICATE"
        page_raw["duplicate_url"] = re.sub(r"/*([?#].*)?$", "", response.url)

        # If we got redirected and url was not yet crawled, add it to frontier
        if re.sub(r"/*([?#].*)?$", "", response.url) not in crawled_urls:
            crawl_logger.info(f"Thread {threadID} Redirected to new url {response.url}")
            add_to_frontier(response.url)

    elif response.ok and response.content and "text/html" in response.headers.get("content-type", ""):
        page_raw['html_content'] = response.text
        # Check if we need to use selenium
        if len(response.text) < 25000:
            crawl_logger.warning(f"Thread {threadID} Using selenium, use count: {selenium_count}")
            # Use selenium
            selenium_response = request_with_selenium(url, web_driver=web_driver, threadID=threadID)
            page_raw['html_content'] = selenium_response
            page_raw["http_status_code"] = 200 if selenium_response else 404
            selenium_count += 1  # Count selenium uses
        page_raw["page_type_code"] = "HTML"
        page_raw["hashcode"] = get_hash(page_raw["html_content"]) if page_raw["html_content"] else None
    elif response.ok and response.content:
        page_raw["page_type_code"] = "BINARY"
        page_raw["page_data"] = {
            "data_type_code": format_page_data(response.headers.get("content-type", "")),
            "data": None
        }
    else:
        bad_response_count += 1
        crawl_logger.warning(f"Thread {threadID} Response not ok, count: {bad_response_count}")

        # If response not ok, we must store a page_raw with only url and http_status_code
        page_raw["http_status_code"] = response.status_code
        page_raw["page_type_code"] = "HTML"

    add_to_crawled_urls(url)
    crawl_logger.warning(f"Thread {threadID} Amount of crawled urls: {len(crawled_urls)} Last crawled url: {url.encode('utf-8')}")

    return page_raw, site_data


def parse_page(page_raw, base_url, conn):
    """Parses HTML content and extract links (urls)."""

    page_obj = {
        "info": page_raw,
        "urls": [],
        "imgs": []
    }

    if page_raw is None:
        return None

    # Page is duplicate, due to redirect
    if page_raw['page_type_code'] == 'DUPLICATE':
        page_obj['info']['page_type_code'] = 'DUPLICATE'
        page_obj['info']['hashcode'] = None
        page_obj['info']['html_content'] = None
        return page_obj

    # Page is duplicate, due to hashcode
    duplicate_url = check_duplicate(conn, page_raw['html_content'], page_raw['url'])
    if duplicate_url:
        page_obj['info']['page_type_code'] = 'DUPLICATE'
        page_obj['info']['duplicate_url'] = duplicate_url
        page_obj['info']['hashcode'] = None
        page_obj['info']['html_content'] = None
        return page_obj

    # Parse HTML and extract links
    soup = BeautifulSoup(page_raw["html_content"], 'html.parser')

    # Find the urls in page
    for link in soup.select('a'):
        found_link = link.get('href')
        if found_link and not found_link.startswith("mailto:"):
            to = urljoin(base_url, found_link)
            clean_to = re.sub(r"/*([?#].*)?$", "", to)
            page_obj['urls'].append({"from_page": base_url, "to_page": clean_to})

    # Find the images in page (<img> tags)
    for img in soup.select('img'):
        found_src = img.get('src')
        if found_src is not None:
            src_full = urljoin(base_url, found_src)

            # Check if src_full is data:image
            if re.match(r"data:image", src_full):
                content_type = re.match(r"(data:image/.*;\s*.*),", src_full).group(1)
                if len(content_type) >= 255:
                    # If content_type is too long, don't save it
                    content_type = ""
                src_full = "BINARY DATA"
            else:
                content_type = os.path.splitext(src_full)[1]

            img_info = {
                "filename": src_full,
                "content_type": content_type,
                "data": None,
                "accessed_time": page_raw["accessed_time"]
            }
            page_obj["imgs"].append(img_info)

    # Find the tags with onclick attribute using BeautifulSoup
    for tag in soup.find_all(onclick=True):
        found_link = tag.get('onclick')
        if found_link is not None:
            # Find the url in the onclick attribute
            valid_links = re.findall(r"(?i)\b(?:(?:https?)://|www\.|/)\S+\b", found_link)
            for link in valid_links:
                if link is not None and not link.startswith("mailto:"):
                    found_link = link
                    to = urljoin(base_url, found_link)
                    clean_to = re.sub(r"/*([?#].*)?$", "", to)
                    page_obj['urls'].append({"from_page": base_url, "to_page": clean_to})
                    crawl_logger.info(f"Found link in onclick attribute: {clean_to.encode('utf-8')}")

    return page_obj


def request_with_selenium(url, web_driver=None, threadID=0):
    """Loads a page with a full web browser to parse javascript"""

    # Crawler should wait for 5 seconds before requesting the page again
    time.sleep(5)

    if web_driver is None:
        web_driver = webdriver.Chrome(service=Service(r'\web_driver\chromedriver.exe'), options=option)

    try:
        web_driver.get(url)
        page = web_driver.page_source
    except Exception as er:
        crawl_logger.warning(f"Thread {threadID} Could not fetch: {er}")
        page = None
    return page


def save_to_db(page_obj, site_data, conn, thread_id):
    """Saves parsed site and page data to DB."""

    if site_data is not None:
        DBManager.insert_site(conn, site_data)

    if page_obj is not None:
        if page_obj['info']['page_type_code'] != 'DUPLICATE':
            add_urls_to_frontier(page_obj['urls'])
        DBManager.insert_all(conn, page_obj['info'], page_obj['urls'], page_obj['imgs'], logging=crawl_logger)
        crawl_logger.info(f"Thread:{thread_id} Processed: {page_obj['info']['url']}")


class Crawler(threading.Thread):
    """A single web crawler instance - continuously runs in own thread."""

    def __init__(self, thread_id, frontier_in, conn, stop_event):
        super().__init__()
        self.threadID = thread_id
        self.frontier = frontier_in
        self.conn = conn
        self.daemon = True
        self.stop_event = stop_event
        self.web_driver = webdriver.Chrome(service=Service(r'\web_driver\chromedriver.exe'), options=option)

    def process_next(self):
        """Fetches, parses and saves next page from frontier."""

        url = get_url_from_frontier()
        page_raw, site_data = request_page(url, web_driver=self.web_driver, threadID=self.threadID)
        page_obj = parse_page(page_raw, url, self.conn)
        save_to_db(page_obj, site_data, self.conn, self.threadID)

    def run(self):
        """Continuously processes pages from frontier."""
        while not self.stop_event.is_set():
            try:
                self.process_next()
            except Exception as er:
                crawl_logger.exception(f"Error: {er}")
                break


if __name__ == '__main__':
    crawl_logger.warning(f"Start Time: {datetime.datetime.now()}")

    try:
        # If it exists, start from a checkpoint frontier
        if os.path.exists("checkpoint.pkl"):
            with open("checkpoint.pkl", "rb") as f:
                frontier, crawled_urls = dill.load(f)
            crawl_logger.warning("Loading frontier!")
    except Exception as e:
        crawl_logger.exception(f"Error: {e}")

    # Add seed urls of domains we want to visit if frontier is empty
    if frontier.empty():
        crawl_logger.warning("Starting from empty frontier!")
        for domain_url in visit_domains:
            add_to_frontier(domain_url)

    crawled_urls = set(crawled_urls)

    # Init base domains
    for page_url in frontier.queue:
        domain_rules[page_url] = None

    NTHREADS = 10  # Use 10
    # Testing different thread amounts for 15 minutes of crawling
    # 1: Amount of crawled urls: 335
    # 2: Amount of crawled urls: 621
    # 4: Amount of crawled urls: 1067
    # 6: Amount of crawled urls: 1261
    # 8: Amount of crawled urls: 1406
    # 10: Amount of crawled urls: 1702
    # 12: Amount of crawled urls: 1640
    # 10: Amount of crawled urls: 1579, Using selenium, use count: 37, len < 20.000
    # 10: Amount of crawled urls: 1266, Using selenium, use count: 383, len < 30.000  <<< good options

    db_manager = DBManager()

    crawlers = []
    event = threading.Event()
    for i in range(NTHREADS):
        crawler = Crawler(i, frontier, db_manager.get_connection(), event)
        crawlers.append(crawler)
        crawler.start()

    crawl_logger.info("Crawlers started")
    # Run crawlers for a set time
    time_start = time.perf_counter()
    time_dif = time_start - time.perf_counter()

    run_time = (7*60)  # In minutes
    while time_dif < (run_time * 60):
        time.sleep(1)
        time_dif = time.perf_counter() - time_start

        # Check if all threads are still alive
        any_crawler_alive = any(crawler.is_alive() for crawler in crawlers)
        if not any_crawler_alive:
            crawl_logger.warning("All crawlers dead, stopping crawlers")
            break

        # Check if ESC was pressed
        if keyboard.is_pressed('esc'):
            crawl_logger.warning("ESC pressed, stopping crawlers")
            break

    # Stopping the threads with event setting
    event.set()

    # Store variables frontier and crawled_urls
    with open('checkpoint.pkl', 'wb') as f:
        dill.dump([frontier, crawled_urls], f)

    crawl_logger.warning(f"Using selenium, use count: {selenium_count}")
