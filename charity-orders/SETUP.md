# White Goods Delivery Orders — Setup Guide

A simple order management app for a charity delivering white goods. Uses Google Sheets as the backend for easy editing and version history.

## Architecture

- **Frontend**: Single HTML file (no build tools needed)
- **Backend**: Google Apps Script web app that reads/writes to a Google Sheet
- **Data**: All order data lives in a Google Sheet that your team can also edit directly

## Setup Steps

### 1. Create the Google Sheet

1. Go to [Google Sheets](https://sheets.google.com) and create a new spreadsheet
2. Name it something like "White Goods Orders"
3. Note: The app will auto-create the "Orders" sheet with headers on first use

### 2. Deploy the Apps Script Backend

1. In your Google Sheet, go to **Extensions → Apps Script**
2. Delete any existing code in `Code.gs`
3. Copy and paste the entire contents of `Code.gs` from this repo
4. Click **Deploy → New deployment**
5. Choose type: **Web app**
6. Set:
   - **Description**: "Orders API"
   - **Execute as**: Me
   - **Who has access**: Anyone (or "Anyone within your organization" if using Google Workspace)
7. Click **Deploy**
8. Authorize the app when prompted
9. Copy the **Web app URL** — you'll need this for the frontend

### 3. Set Up the Frontend

#### Option A: Open locally
Simply open `index.html` in a browser. Paste the Web App URL when prompted.

#### Option B: Host on GitHub Pages
1. Push this folder to a GitHub repo
2. Go to Settings → Pages → set source to main branch
3. Access via `https://yourusername.github.io/repo-name/charity-orders/`

#### Option C: Any static host
Upload `index.html` to Netlify, Vercel, or any static hosting service.

### 4. Share with Your Team

- Share the Google Sheet with your 5 team members (Editor access)
- Share the frontend URL with them
- They can either use the web app OR edit the Google Sheet directly — both stay in sync

## How It Works

### Web App
- **Create orders** with recipient details, item type, delivery date, and volunteer assignment
- **Filter** by status (Pending / Scheduled / Delivered / Cancelled)
- **Edit** any order by clicking on it
- **Delete** orders if needed

### Google Sheet
- All data is in the "Orders" sheet
- You can edit any cell directly in the sheet
- Column A (ID) should not be changed
- Column B/C (Created/Updated) are timestamps
- Version history is automatic via Google Sheets

## Order Fields

| Field | Description |
|-------|-------------|
| Status | Pending, Scheduled, Delivered, or Cancelled |
| Recipient Name | Person receiving the item |
| Phone | Contact number |
| Address | Delivery address |
| Postcode | Postcode |
| Item Type | Washing Machine, Fridge, Cooker, etc. |
| Item Description | Brand, model, condition notes |
| Assigned To | Volunteer handling delivery |
| Delivery Date | Planned delivery date |
| Notes | Any additional information |

## Troubleshooting

**"Error loading orders"**: Check that your API URL is correct and the Apps Script is deployed as a web app.

**CORS issues**: Google Apps Script handles CORS automatically for deployed web apps. Make sure you're using the `/exec` URL, not the `/dev` URL.

**Changes not appearing**: Click "Refresh" in the app. The Google Sheet is the source of truth.

**Need to redeploy**: If you change `Code.gs`, go to Deploy → Manage deployments → edit the existing deployment and click Deploy.
