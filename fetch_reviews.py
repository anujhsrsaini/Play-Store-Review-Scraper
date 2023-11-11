#Import required libraries
from google_play_scraper import app, Sort, reviews_all, search, reviews
import pandas as pd

#Search for apps for google playstore
keyword = "News"
result = search(
    keyword,
    lang="en",  # defaults to 'en'
    country="in",  # defaults to 'us'
    n_hits=10  # defaults to 30 (= Google's maximum)
)

#Store search resuts
search_results = pd.DataFrame(result)
all_app_data = []

#Receive detailed app information one by one and store in all_app_data
for idx, row in search_results.iterrows():
    app_data = app(
    row['appId'],
    lang='en', # defaults to 'en'
    country='in' # defaults to 'us'
    )
    all_app_data.append(app_data)

#convert list of dictionary to pandas dataframe
data = pd.DataFrame(all_app_data)

#Dropping unnecessary documents
data.drop(['installs','minInstalls','free','currency','sale','saleTime','originalPrice','saleText','offersIAP','inAppProductPrice','developer','developerId',\
            'developer', 'developerEmail','developerWebsite','developerAddress','privacyPolicy','genre','genreId','categories',
            'headerImage','screenshots','video','videoImage','contentRating','contentRatingDescription','adSupported','updated','version','comments'], axis=1, inplace=True)

#Save app infor to csv file
data.to_csv("Top10_"+keyword+"_Apps.csv")
print("Fetched top 10 "+keyword+" apps")

#Fetch latest 20k reviews of above apps one by one and save to specific csv files
for idx, row in search_results.iterrows():
    print("Fetching reviews for " + row['appId'])
    g_reviews, token = reviews(
                        row['appId'],
                        lang='en', # defaults to 'en'
                        country='in', # defaults to 'us'
                        sort=Sort.NEWEST, # defaults to Sort.MOST_RELEVANT
                        count = 20000
                    )
    pd.DataFrame(g_reviews).to_csv("reviews/"+str(row['appId'])+".csv") 