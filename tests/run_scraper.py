from scraper.web_scraper import bulk_scrape, save_csv

urls = [
    "https://www.dpsvasantkunj.com",
    "https://www.dpsrkp.net",
    "https://www.modernschool.net",
    "https://www.balbharati.org",
    "https://www.sanskritischool.edu.in",
    "https://www.amity.edu",
"https://www.apeejay.edu",
"https://www.springdales.com",
"https://www.tagoreint.com",
"https://www.birla.ac.in",
"https://www.mothersinternational.in",
"https://www.vasantvalley.org",
"https://www.gdgoenka.com",
"https://www.dpsmathuraroad.com",
"https://www.dpsnoida.co.in",
]

results = bulk_scrape(urls)

save_csv(results, "data/schools.csv")

print(f"Saved {len(results)} schools")