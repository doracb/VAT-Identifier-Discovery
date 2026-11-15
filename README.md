# The initial research

The task is to figure out whether a UK company VAT dataset can be built from the open web, then prove it using a sample. This is needed for a mid-sized manufacturing company that pays invoices to 40000 UK suppliers every month.

I decided to use the official public sector website of the United Kingdom government, **GOV.UK**, to research how this exchange between companies works. According to them, invoices should include: a unique identification number, the company name, address and contact information, the company name and address of the customer being invoiced, a clear description of what is being charged for, the date the goods or service were provided (supply date), the date of the invoice, the amount(s) being charged, the VAT amount if applicable, and the total amount owed. In the case of sole traders, their name and any business name being used, plus an address where any legal documents can be delivered if they are using a business name, must also be included.

However, what really interests us are **VAT invoices**, which are issued exclusively by a VAT-registered business. The VAT registration number is the only identifier that appears on both the invoice and the tax record, which makes matching an invoice to a supplier record easy. But the company’s procurement team only know the VAT number of a third of their suppliers, and they’re forced to guess for the rest using the name, which is rather unreliable.

Finding the name of UK companies is the easy part. The **Companies House “Free Company Data Product”** is a monthly updated dataset basic company data of live companies in the UK. While there is a lot of useful information we can get by analyzing it, the VAT numbers we’re looking for are unfortunately not there.

But what are VAT numbers anyway? Well, according to swiftvatpro.co.uk, the short answer is that a **UK VAT number** (often shortened as VRN) is a *unique tax identifier that must appear on every VAT invoice the business issues*, and customers need it to reclaim the VAT they are charged.
It is issued by **His Majesty’s Revenue and Customs (HMRC)** upon registration. Standard UK businesses receive a 9-digit number prefixed with GB, but branch registrations use 12 digits, with the last 3 identifying the branch.

One important thing to note is that the VRN is assigned independently of the company’s Companies House Registration Number (CRN), and there is no mathematical or public lookup mapping between the two.

HMRC does provide a web tool that allows you to **check if a UK VAT registration number is valid** and to find the name and address of the business the number is registered to. There’s also an API that can be used, but it requires formal developer registration with an approval wait time. Regardless, searching doesn’t work the other way around, due to taxpayer confidentiality rules.

While researching the VRN’s format, I’ve found that there’s a way to discard malformed VAT numbers by using its **built-in checksum**. Out of 9 digits, the last two are the check digits. By multiplying the first 7 digits individually by the weights 8, 7, 6, 5, 4, 3, and 2 respectively, summing all 7 products, then adding the two check digits, the result should be divisible by 97 for the VRN to be valid. Newer numbers use the same method, but with 55 added to the total first.

Probably the most important part of this research is figuring out *where to actually find the VAT numbers*. HMRC states that VAT numbers for most businesses and organisations can be found on their **invoices, receipts or website**. UK businesses that import or export goods also use an **Economic Operators Registration and Identification (EORI) number** formatted as GB + [9-digit VRN] + 000, so finding an EORI number on a customs or shipping page would reveal the VRN indirectly.

The problem is that many companies may not have a VRN at all for various reasons, so I decided to investigate why.

I have found that businesses have to register for VAT only if their VAT taxable turnover is more than £90,000. You must also be registered if you’re based outside the UK, your business is based outside the UK or if you supply any goods or services to the UK (or expect to in the next 30 days). However, you can make a “voluntary registration” for a VAT even if your turnover is less than £90,000, so we shouldn’t immediately eliminate smaller companies.

There are also goods and services that are **VAT-exempt or out of scope** in the UK (gov.uk/guidance/rates-of-vat-on-different-goods-and-services), including commercial property leasing, financial and insurance services, employee education/training, and statutory or postal fees. Even a mid-sized manufacturer regularly pays invoices to landlords, insurers, banks, and training providers, which means that some suppliers in the procurement team's list will never hold a VRN regardless of their size.

In this year’s annual report (Companies register activities: statistical release April 2025 to March 2026), it is stated that there were **5,479,045 companies** by March 31st, 2026. The effective register size, which does not include companies in the process of dissolution or liquidation, was 4,930,634 at the end of FYE 2026. There’s also the VAT annual statistics (the latest being the 2024 to 2025 report), in which it is said that there were **2,285,900 live VAT traders** at the end of FY 2024-2025. This figure covers all legal trader types, not just incorporated companies, so it includes sole traders, partnerships and non-profits.

What we can gather from this is that **fewer than half of live UK companies actually hold a VAT registration**. If we want to scrape the internet to find VAT numbers, one company’s VRN not being found could mean one of four things: the pipeline meant to find it failed, the business is below the £90,000 threshold, it operates exclusively in a VAT-exempt sector, or the company is inactive.

# Exploratory Data Analysis of the Companies House snapshot

I decided to take a closer look at the latest Companies House “Free Company Data Product” dataset next. There was the option to download it as multiple files, but seeing Excel load 849,999 rows for just one seventh of the dataset (the spreadsheet cap is at 1,048,576 rows), it became clear that relying on spreadsheet software was a dead end. Another reason why this approach was bad was that, in order to get an unbiased sample across the entire alphabet, I would need to have all the data in one place. I decided to process it as one big file using a Jupyter Notebook in VS Code instead.

Loading the full BasicCompanyDataAsOneFile-2026-11-01.csv snapshot (55 columns) in Python revealed several clear filtering signals for isolating plausible B2B suppliers:
- **Company Status & Accounts:** Out of 5,179,256 Active companies, **532,303** records in the full file are in strike-off, liquidation, or insolvency, and **623,597** file DORMANT accounts. Another **1,465,114** list NO ACCOUNTS FILED (largely newly incorporated entities).
- **Corporate Structure:** *Private Limited Company* makes up **4,761,802 (91.9%)** of active records, whereas **417,454** are *non-standard structures* such as guarantee companies, charities, community interest companies, and partnerships.
- **Industry (SIC) Skew:** Just three real estate and property management codes (68209, 68100, 98000) account for **650,871** active companies (12.6% of all active records), which are predominantly VAT-exempt property vehicles, alongside **117,903** holding companies (64209), **74,960** human health entities (86900), and **95,468** Active entities explicitly classified under 99999 - Dormant Company.

# Candidate Open-Web Discovery Sources

The URI field that can be found in the Companies House dataset leads to government linked-data records rather than commercial websites, and there are no tax identifiers either, so I’ve thought of four separate paths to investigate:

1.	**Direct Company Websites (Legal Disclosures):** Under UK Electronic Commerce (2002) and Companies (Trading Disclosures) (2008) regulations, businesses trading online must display their VAT number on their website (typically in the footer, /terms, /privacy, or /contact pages). The problems here are that website URLs cannot be found in a public dataset, and WHOIS domain lookups are blocked by GDPR privacy redactions.
2.	**Indexed Web Mentions & Bulk Corpora (Common Crawl / Search Dorking):** Rather than crawling sites individually, querying pre-crawled corpora like Common Crawl or targeted web searches ("Company Name", "VAT") can surface numbers from indexed invoices or directories, though Common Crawl often captures homepages while missing deeper legal subpages.
3.	**Public Procurement Records (Contracts Finder / Find a Tender):** Government contract award notices sometimes publish winning suppliers' VAT numbers, though coverage is restricted to public-sector contractors.
4.	Insolvency Notices: Official insolvency notices often publish a company's VRN, but only after the company enters liquidation. We need active suppliers, so this path is a pretty clear dead end.
