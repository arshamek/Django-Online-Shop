# WebScrapper assessment

## Finding

No component, module, class, function or file called `WebScrapper` (or any spelling of scraper/crawler, or any
scraping library: requests-html, BeautifulSoup, Selenium, Playwright, Scrapy, Puppeteer, Cheerio) exists in the
uploaded `maral-online-shop` archive: backend, frontend or docs. The only mentions of scraping are in
`docs/frontend-analysis/FRONTEND_GAPS.md` (an open question about how Trendyol/Namshi data would be sourced)
and `docs/architecture/TECHNICAL_DECISIONS.md` D-17 (scraping and anti-bot circumvention excluded).

It may live in another repository or an unmerged branch. **Nothing was reused or refactored**, and the engine
was built to be independent of it.

## What to do with it when you find it

Evaluate it against the same criteria used for the providers before deciding anything:

| Criterion | Question |
|---|---|
| Functionality | Does it fetch HTML pages, call a private/mobile API, or call a documented API? |
| Compatibility | Can it return `ProviderPage`/`ProviderProduct` (providers/dto.py)? |
| Error handling | Does it distinguish 401/403/404/429/5xx and timeouts (providers/errors.py)? |
| Maintainability | Selectors/endpoints hard-coded to page markup break on every redesign |
| Security | Host allow-list, redirects, size and time limits, no secrets in logs (providers/http.py) |
| Legal compliance | Is there written permission from the source for this access and for republishing the data? |
| Extensibility | Can it sit behind `BaseTransport` without leaking into the pipeline? |

Decision rules:

* If it reads public web pages, evades bot protection, rotates IPs/User-Agents, or ignores robots/terms/rate
  limits: **do not integrate it**, wrap it, or call it from a provider. This is a requirement of the phase.
* If it is a plain HTTP client for an API you are authorised to use: it may be reused as a `BaseTransport`
  implementation, but `SafeHttpClient` already covers that need.
* A `BaseProductProvider` may only be backed by something that passes the authorisation gate
  (`providers/authorization.py`), never by "WebScrapper has access".

Share the component (or its path) and I will assess it against this table.
