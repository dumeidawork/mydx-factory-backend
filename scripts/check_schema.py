import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.core.database import fetch_all

def check():
    print("order_details columns:")
    for row in fetch_all("SHOW COLUMNS FROM order_details"):
        print(row['Field'])
    print("\nheat_treatment_trial_records columns:")
    for row in fetch_all("SHOW COLUMNS FROM heat_treatment_trial_records"):
        print(row['Field'])

if __name__ == '__main__':
    check()
