## Python Playwright template

<!-- This is an Apify template readme -->

## Included features

- **[Apify SDK](https://docs.apify.com/sdk/python/)** for Python - a toolkit for building Apify [Actors](https://apify.com/actors) and scrapers in Python
- **[Input schema](https://docs.apify.com/platform/actors/development/input-schema)** - define and easily validate a schema for your Actor's input
- **[Request queue](https://docs.apify.com/sdk/python/docs/concepts/storages#working-with-request-queues)** - queues into which you can put the URLs you want to scrape
- **[Dataset](https://docs.apify.com/sdk/python/docs/concepts/storages#working-with-datasets)** - store structured data where each object stored has the same attributes
- **[Playwright](https://pypi.org/project/playwright/)** - a browser automation library

## Resources

- [Playwright for web scraping in 2023](https://blog.apify.com/how-to-scrape-the-web-with-playwright-ece1ced75f73/)
- [Scraping single-page applications with Playwright](https://blog.apify.com/scraping-single-page-applications-with-playwright/)
- [How to scale Puppeteer and Playwright](https://blog.apify.com/how-to-scale-puppeteer-and-playwright/)
- [Integration with Zapier](https://apify.com/integrations), Make, GitHub, Google Drive and other apps
- [Video guide on getting data using Apify API](https://www.youtube.com/watch?v=ViYYDHSBAKM)
- A short guide on how to build web scrapers using code templates:

[web scraper template](https://www.youtube.com/watch?v=u-i-Korzf8w)


## Getting started

For complete information [see this article](https://docs.apify.com/platform/actors/development#build-actor-at-apify-console). In short, you will:

1. Build the Actor
2. Run the Actor

## Pull the Actor for local development

If you would like to develop locally, you can pull the existing Actor from Apify console using Apify CLI:

1. Install `apify-cli`

    **Using Homebrew**

    ```bash
    brew install apify-cli
    ```

    **Using NPM**

    ```bash
    npm -g install apify-cli
    ```

2. Pull the Actor by its unique `<ActorId>`, which is one of the following:
    - unique name of the Actor to pull (e.g. "apify/hello-world")
    - or ID of the Actor to pull (e.g. "E2jjCZBezvAZnX8Rb")

    You can find both by clicking on the Actor title at the top of the page, which will open a modal containing both Actor unique name and Actor ID.

    This command will copy the Actor into the current directory on your local machine.

    ```bash
    apify pull <ActorId>
    ```

## Documentation reference

To learn more about Apify and Actors, take a look at the following resources:

- [Apify SDK for JavaScript documentation](https://docs.apify.com/sdk/js)
- [Apify SDK for Python documentation](https://docs.apify.com/sdk/python)
- [Apify Platform documentation](https://docs.apify.com/platform)
- [Join our developer community on Discord](https://discord.com/invite/jyEM2PRvMU)

---

## 🚀 Project-Specific Description — Craigslist Renew/Repost Actor

### Purpose  
This actor automates the task of renewing or reposting job listings on Craigslist.  
It serves as the pilot agent for the back-office automation system being developed for Cafe Eikeiwa.

### What the Actor Does  
- Logs into Craigslist using credentials stored in Apify Secrets (`CL_EMAIL`, `CL_PASSWORD`)  
- Navigates to the account management page:  
  `https://accounts.craigslist.org/login/home`  
- Reads the “Manage Postings” table  
- Detects which postings have a **repost** or **renew** button available  
- Clicks to renew/repost eligible listings  
- Captures before/after screenshots  
- Outputs a structured JSON summary of actions taken  
- Stores screenshots and logs in the Apify Key-Value Store  
- Optional: pushes structured results to a Dataset for historical logs

---

## 🧪 Running Locally (Developer)

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install Playwright browsers
playwright install

# 3. Set environment variables (Windows PowerShell example)
setx CL_EMAIL "rarizard.r@gmail.com"
setx CL_PASSWORD "Rufuslist98"

# 4. Run the actor
apify run
```

## ⚙️ Input Parameters (input_schema.json)
| Field        | Type     | Description                                                   |
|--------------|----------|---------------------------------------------------------------|
| mode         | string   | Accepts "repost", "renew", or "dry-run"                       |
| screenshots  | string   | Accepts "none", "summary", or "per-action"                    |
| delays       | object   | Random delay range in milliseconds to simulate human behaviour |
| timeout_sec  | integer  | Maximum allowed run time                                      |
| headless     | boolean  | Whether to run browser headless                               |

### Example Input
{
  "mode": "repost",
  "screenshots": "summary",
  "delays": { "min": 300, "max": 1200 },
  "timeout_sec": 180,
  "headless": true
}

## 💾 Output and Storage
Key Value Store

Files saved:
- before.png
- after.png
- summary.json
- Optional. page.html snapshot for debugging

### Optional Dataset

Each row may look like:
```json
{
  "listing_id": "123456789",
  "title": "Test Posting",
  "action": "repost",
  "status": "success",
  "timestamp": "2025-11-20T12:00:00Z"
}
```

## 📝 Next Steps. TODO
- Implement login flow using secure environment variables
- Navigate to Manage Postings and parse table rows
- Detect presence of repost or renew links
- Implement click behaviour with error handling and retries
- Store screenshots and generate a clean summary JSON
- Integrate with Make.com for scheduling and notifications
- Prepare for expansion into the more complex cafe reservation automation that requires flexible AI driven task selection and browser action handling

test commit