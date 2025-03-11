import scrapy
import re
import time
import json
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from scrapy.selector import Selector

# Define separate items for films and actors
class Film(scrapy.Item):
    title = scrapy.Field()
    year = scrapy.Field()
    directors = scrapy.Field()
    countries = scrapy.Field()
    production_budget = scrapy.Field()
    box_office = scrapy.Field()
    imdb = scrapy.Field()
    metascore = scrapy.Field()
    num_of_awards = scrapy.Field()
    num_of_nominations = scrapy.Field()
    genres = scrapy.Field()
    actors = scrapy.Field()
    film_type = scrapy.Field()
    link = scrapy.Field()

class Actor(scrapy.Item):
    id = scrapy.Field()
    name = scrapy.Field()
    surname = scrapy.Field()
    popularity = scrapy.Field()
    url = scrapy.Field()

class ImdbFilmSpider(scrapy.Spider):
    name = 'imdb_film'
    allowed_domains = ['imdb.com']
    start_urls = ['https://www.imdb.com/search/title/?genres=!documentary,!short&explore=genres']
    
    # File path for storing film data
    OUTPUT_FILE = 'films_data.json'
    
    # Modes of operation
    MODE_COLLECT = 'collect'
    MODE_ENRICH = 'enrich'

    custom_settings = {
        'USER_AGENT': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36',
        'ROBOTSTXT_OBEY': False,
        'DOWNLOAD_DELAY': 1,
        'LOG_LEVEL': 'INFO',
        'LOG_FORMAT': '%(message)s',
        'LOGGING_ENABLED': False,
        'LOG_STDOUT': False,
        'LOGGING_SETTINGS': {
            'loggers': {
                'scrapy': {'level': 'CRITICAL'},
                'scrapy.core.engine': {'level': 'CRITICAL'},
                'scrapy.extensions': {'level': 'CRITICAL'},
                'scrapy.middleware': {'level': 'CRITICAL'},
                'selenium': {'level': 'CRITICAL'},
                'urllib3': {'level': 'CRITICAL'},
                'webdriver_manager': {'level': 'CRITICAL'},
            }
        }
    }

    # Use a dict to track actor IDs and popularity to avoid duplicate actor requests
    actor_seen = {}
    # Use a set to track collected films to avoid duplicates
    films_seen = set()
    
    def __init__(self, max_pages=500, mode=MODE_COLLECT, *args, **kwargs):
        super(ImdbFilmSpider, self).__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.mode = mode
        
        # Only initialize Selenium if we're in collect mode
        if self.mode == self.MODE_COLLECT:
            chrome_options = webdriver.ChromeOptions()
            # Add options to make Chrome more stable and faster for scraping
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--proxy-server='direct://'")
            chrome_options.add_argument("--proxy-bypass-list=*")
            chrome_options.add_argument("--start-maximized")
            chrome_options.add_argument("--headless")
            
            self.driver = webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install()),
                options=chrome_options
            )

    def start_requests(self):
        if self.mode == self.MODE_COLLECT:
            # Start with the main film list page
            for url in self.start_urls:
                yield scrapy.Request(url, callback=self.parse_list)
        elif self.mode == self.MODE_ENRICH:
            # Load existing film data and enrich it
            if os.path.exists(self.OUTPUT_FILE):
                with open(self.OUTPUT_FILE, 'r') as f:
                    films_data = json.load(f)
                    
                self.logger.info(f"Loaded {len(films_data)} films for enrichment")
                
                for film_data in films_data:
                    if 'link' in film_data and film_data['link']:
                        yield scrapy.Request(
                            url=film_data['link'],
                            callback=self.parse_film_detail,
                            meta={'film': film_data},
                            dont_filter=True
                        )
                    else:
                        self.logger.warning(f"Film {film_data.get('title')} has no link, skipping")
            else:
                self.logger.error(f"File {self.OUTPUT_FILE} not found. Run in 'collect' mode first.")
        
    def parse_list(self, response):
        """First phase: Extract basic film data using Selenium"""
        # Start Selenium session
        self.logger.info("Opening URL with Selenium: %s", response.url)
        self.driver.get(response.url)
        
        # Wait for the page to load
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "li.ipc-metadata-list-summary-item"))
        )
        
        pages_loaded = 0
        all_films = []
        
        # Process current page and click "50 more" button repeatedly
        while pages_loaded < self.max_pages:
            # Process current page content
            self.logger.info(f"Processing page {pages_loaded + 1}")
            
            # Extract films from current page state
            page_content = self.driver.page_source
            selector = Selector(text=page_content)
            
            # Process new films on current page
            new_films = self._extract_basic_film_data(selector)
            all_films.extend(new_films)
            
            # Save intermediate results
            self._save_films(all_films)
            
            # Try to find and click the "Load more" button
            try:
                # Scroll to bottom to ensure the button is visible
                time.sleep(1)
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)  # Allow time for the page to settle
                
                # Look for the "50 more" button
                load_more_button = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//button[.//span[contains(text(), '50 more')]]"))
                )
                
                # Scroll specifically to the button
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_button)
                time.sleep(1)  # Allow time for the scroll
                
                # Try to click using JavaScript
                self.logger.info("Clicking '50 more' button")
                self.driver.execute_script("arguments[0].click();", load_more_button)
                
                # Wait for new content to load
                time.sleep(3)  # Allow time for content to load
                
                pages_loaded += 1
            
            except (TimeoutException, NoSuchElementException) as e:
                self.logger.info(f"No more '50 more' button found or error: {str(e)}")
                break
        
        # Final save and close browser
        self._save_films(all_films)
        self.driver.quit()
        
        self.logger.info(f"Completed collection of {len(all_films)} films")
    
    def _extract_basic_film_data(self, response):
        """Extract basic film data from the current page"""
        films = response.css('li.ipc-metadata-list-summary-item')
        new_films = []
        
        for film in films:
            # Extract title and clean ranking prefix
            raw_title = film.css('h3.ipc-title__text::text').get()
            if not raw_title:
                continue
                
            title = re.sub(r'^\d+\.\s*', '', raw_title)

            # Extract year from metadata
            metadata = film.css('span.dli-title-metadata-item::text').getall()
            year = None
            if metadata:
                m = re.search(r'(\d{4})', metadata[0])
                if m:
                    year = int(m.group(1))

            if year and year < 1990:
                continue
            
            # Skip TV Series or TV Mini Series
            film_type = film.css('span.dli-title-type-data::text').get()
            if film_type and ("TV Series" in film_type or "TV Mini Series" in film_type):
                continue

            # Extract IMDb rating
            imdb_rating_text = film.css('span.ipc-rating-star--rating::text').get()
            imdb_rating = float(imdb_rating_text) if imdb_rating_text else None

            # Build film detail URL
            film_url_relative = film.css('a.ipc-title-link-wrapper::attr(href)').get()
            if not film_url_relative:
                continue
                
            film_url = f"https://www.imdb.com{film_url_relative}" if film_url_relative.startswith('/') else film_url_relative
            
            # Generate a unique key for this film to avoid duplicates
            film_key = f"{title}_{year}"
            
            # Only add if we haven't seen this film before
            if film_key not in self.films_seen:
                film_data = {
                    'title': title,
                    'year': year,
                    'imdb': imdb_rating,
                    'link': film_url,
                    'film_type': film_type if film_type else '',
                    # Initialize empty fields
                    'directors': [],
                    'countries': [],
                    'production_budget': None,
                    'box_office': None,
                    'metascore': None,
                    'num_of_awards': None,
                    'num_of_nominations': None,
                    'genres': [],
                    'actors': []
                }
                
                new_films.append(film_data)
                self.films_seen.add(film_key)
                self.logger.info(f"Added new film: {title} ({year}) - {film_url}")
        
        return new_films

    def _save_films(self, films):
        """Save films to JSON file"""
        with open(self.OUTPUT_FILE, 'w') as f:
            json.dump(films, f, indent=2)
        self.logger.info(f"Saved {len(films)} films to {self.OUTPUT_FILE}")

    def parse_film_detail(self, response):
        """Second phase: Enrich film data with details from its page"""
        film = response.meta['film']
        self.logger.info(f"Enriching data for film: {film.get('title')} (status {response.status})")

        if response.status != 200:
            self.logger.warning(f"Non-200 response for {film.get('title')}; skipping.")
            return

        # Extract directors (remove duplicates)
        directors = list(set(response.xpath(
            '//li[@data-testid="title-pc-principal-credit"][.//span[contains(text(),"Director")]]'
            '//a[contains(@href, "/name/")]/text()'
        ).getall()))
        film['directors'] = directors

        # Extract countries
        film['countries'] = response.xpath('//li[@data-testid="title-details-origin"]//a/text()').getall()

        # Extract production budget and box office using helper
        film['production_budget'] = self.parse_money(response.xpath('//li[@data-testid="title-boxoffice-budget"]//text()').getall())
        film['box_office'] = self.parse_money(response.xpath('//li[@data-testid="title-boxoffice-cumulativeworldwidegross"]//text()').getall())

        # Extract metascore
        metascore_text = response.xpath('//span[contains(@class, "metacritic-score-box")]/text()').get()
        film['metascore'] = int(metascore_text.strip()) if metascore_text and metascore_text.strip().isdigit() else None

        # Extract genres (using the working structure)
        film['genres'] = response.xpath('//div[@data-testid="interests"]//span[contains(@class,"ipc-chip__text")]/text()').getall()

        # Extract actors
        actors = []
        cast_rows = response.xpath('//div[@data-testid="title-cast-item"]')
        for row in cast_rows:
            full_name = row.xpath('.//a[contains(@data-testid, "title-cast-item__actor")]/text()').get()
            actor_url_relative = row.xpath('.//a[contains(@data-testid, "title-cast-item__actor")]/@href').get()
            if full_name and actor_url_relative:
                parts = full_name.split()
                name = parts[0]
                surname = parts[-1] if len(parts) > 1 else ''
                actor_url = response.urljoin(actor_url_relative)
                actor_id_match = re.search(r'/name/(nm\d+)/', actor_url)
                actor_id = actor_id_match.group(1) if actor_id_match else None
                actor_data = {
                    'name': name,
                    'surname': surname,
                    'popularity': 0,  # placeholder
                    'url': actor_url,
                    'id': actor_id
                }
                actors.append(actor_data)
                if actor_id and actor_id not in self.actor_seen:
                    self.actor_seen[actor_id] = 0 # placeholder
                    yield scrapy.Request(
                        url=actor_url,
                        callback=self.parse_actor,
                        meta={'actor_data': actor_data, 'film': film},
                        dont_filter=True
                    )
                elif actor_id and actor_id in self.actor_seen:
                    actor_data['popularity'] = self.actor_seen[actor_id]
                    if film:
                        for actor in film['actors']:
                            if actor['id'] == actor_data['id']:
                                actor['popularity'] = actor_data['popularity']
                                break
                        
                        # Save the updated film data
                        self._update_film_in_json(film)

        film['actors'] = actors

        # Schedule awards page request
        film_url = response.url
        film_id_match = re.search(r'/title/(tt\d+)/', film_url)
        if film_id_match:
            film_id = film_id_match.group(1)
            awards_url = response.urljoin(f'/title/{film_id}/awards/')
            self.logger.info(f"Scheduling awards page for film {film.get('title')}: {awards_url}")
            yield scrapy.Request(
                url=awards_url,
                callback=self.parse_awards,
                meta={'film': film},
                dont_filter=True
            )
        else:
            # Save the updated film data
            self._update_film_in_json(film)

    def parse_awards(self, response):
        film = response.meta['film']
        self.logger.info(f"Parsing awards for film: {film.get('title')}")
        awards_text = response.css('div[data-testid="awards-signpost"] div.ipc-signpost__text::text').get()
        wins = 0
        nominations = 0
        if awards_text:
            wins_match = re.search(r'(\d+)\s+wins', awards_text, re.IGNORECASE)
            noms_match = re.search(r'(\d+)\s+nominations', awards_text, re.IGNORECASE)
            wins = int(wins_match.group(1)) if wins_match else 0
            nominations = int(noms_match.group(1)) if noms_match else 0
        film['num_of_awards'] = wins
        film['num_of_nominations'] = nominations
        
        # Save the updated film data
        self._update_film_in_json(film)

    def parse_actor(self, response):
        actor_data = response.meta['actor_data']
        film = response.meta.get('film')
        
        popularity_text = response.css('span.starmeter-difference::text').get()
        popularity = int(popularity_text.strip()) if popularity_text and popularity_text.strip().isdigit() else 0
        actor_data['popularity'] = popularity
        self.actor_seen[actor_data['id']] = popularity
        self.logger.info(f"Actor {actor_data['name']} {actor_data['surname']} popularity: {popularity}")
        
        # If we have a film reference, update the actor in the film's data
        if film:
            for actor in film['actors']:
                if actor['id'] == actor_data['id']:
                    actor['popularity'] = popularity
                    break
            
            # Save the updated film data
            self._update_film_in_json(film)

    def _update_film_in_json(self, updated_film):
        """Update a specific film in the JSON file"""
        try:
            with open(self.OUTPUT_FILE, 'r') as f:
                films = json.load(f)
            
            # Find and update the matching film
            for i, film in enumerate(films):
                if film['title'] == updated_film['title'] and film['year'] == updated_film['year']:
                    films[i] = updated_film
                    break
            
            # Write the updated data back
            with open(self.OUTPUT_FILE, 'w') as f:
                json.dump(films, f, indent=2)
                
            self.logger.info(f"Updated film: {updated_film['title']}")
        except Exception as e:
            self.logger.error(f"Error updating film in JSON: {e}")

    def parse_money(self, text_list):
        text = " ".join(text_list).strip()
        match = re.search(r'\$([\d,]+)', text)
        if match:
            money_str = match.group(1).replace(',', '')
            try:
                return int(money_str)
            except ValueError:
                return None
        return None
 