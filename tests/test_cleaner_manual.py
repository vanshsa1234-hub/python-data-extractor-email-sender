from scraper.cleaner import clean_leads

leads = [
    {
        "email": "principal@dpsvasantkunj.com",
        "name": " principal ",
        "phone": "+91-11-43261200"
    },
    {
        "email": "principal@dpsvasantkunj.com",
        "name": "duplicate",
        "phone": ""
    },
    {
        "email": "invalid-email",
        "name": "bad"
    }
]

result = clean_leads(leads)

for r in result:
    print(r)