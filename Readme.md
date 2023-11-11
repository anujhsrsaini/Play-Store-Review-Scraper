# Google Play Store News App Reviews Scraper

![Python](https://img.shields.io/badge/Python-3.7.9-blue.svg)

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Authors](#authors)

## Introduction

This Python script allows you to scrape information about the top 10 news (can be changed as per user preference) apps from the Google Play Store and collect their user reviews into CSV files. The collected data can be used for analysis of user sentiments, ratings, and feedback for these apps.

## Features

- Search for the top 10 news (or user defined keyword) apps on the Google Play Store.
- Fetch detailed information about each app, including its name, developer, description, and more.
- Download user reviews for each app, including ratings, comments, and timestamps.
- Save app information and user reviews into separate CSV files for further analysis.

## Installation

1. Clone the repository:

   ```sh
   git clone https://github.com/anujhsrsaini/Play_Store_Review_Scraper.git
   ```
2. Navigate to the project directory:
   ```sh 
   cd Play_Store_Review_Scraper
   ```
3. Initiate a virtual environment and activate the virtual environment:
   ```sh 
   python -m venv venv
   venv/Scripts/activate
   ```
3. Install the required libraries in the virtual environment
   ```sh
   pip install -r requirements.txt
   ```
4. You can make certain changes to the code to search for your desired keywords.

## Usage

Run the main script to fetch app information and user reviews:

```sh
python fetch_reviews.py
```

This script performs the following steps:

* Searches for the top 10 news apps on the Google  Play Store in a specific country ***(default: India).***
* Retrieves detailed app information for each of the top 10 apps.
* Stores the app information in a CSV file named Top10_NewsApps.csv.
* For each app, it downloads user reviews, saving them in separate CSV files named after the app's package name.
* You can analyze the collected data using your preferred data analysis tools, such as Python, SQL, or Tableau.

## Authors

- **[Anuj Saini](https://www.linkedin.com/in/anuj-saini-7230a0257/)**