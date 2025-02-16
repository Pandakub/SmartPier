from flask import Flask, render_template, jsonify
import requests
import threading
import subprocess
import time
import datetime
from flask_socketio import SocketIO
import pandas as pd
import numpy as np
import pytz

app = Flask(__name__)

# กำหนด timezone ที่จะใช้ทั่วทั้งแอพ
TIMEZONE = pytz.timezone('Asia/Bangkok')

FLAG_COLORS = {
    "ธงส้ม": "orange",
    "ธงเขียวเหลือง": "green",
    "ธงเหลือง": "lightyellow",
    "ธงเหลืองรอบบ่าย": "yellow",
    "ธงแดง": "red",
    "เรือไฟฟ้า": "purple"
}

def get_current_time():
    """ฟังก์ชันสำหรับดึงเวลาปัจจุบันในเขตเวลาไทย"""
    return datetime.datetime.now(TIMEZONE)

def get_boat_schedule():
    return {
        "station": "N24 ท่าพระรามเจ็ด",
        "time_now": get_current_time().strftime("%H:%M"),
        "routes": [
            {
                "direction": "ท่าน้ำนนท์ - ปากเกร็ด",
                "schedule": [
                    {"type": "ธงส้ม", "time": "16:03", "color": "orange"},
                    {"type": "ธงเขียวเหลือง", "time": "16:44", "color": "green"},
                    {"type": "ธงเหลือง", "time": "17:56", "color": "lightyellow"},
                    {"type": "ธงเหลืองรอบบ่าย", "time": "13:50", "color": "yellow"},
                    {"type": "เรือไฟฟ้า", "time": "14:50", "color": "purple"}
                ]
            },
            {
                "direction": "สาทร - วัดราชสิงขร",
                "schedule": [
                    {"type": "ธงส้ม", "time": "15:18", "color": "orange"},
                    {"type": "ธงเขียวเหลือง", "time": "ไม่มีรอบถัดไป", "color": "green"},
                    {"type": "ธงเหลือง", "time": "ไม่มีรอบถัดไป", "color": "lightyellow"},
                    {"type": "ธงเหลืองรอบบ่าย", "time": "14:00", "color": "yellow"},
                    {"type": "ธงแดง", "time": "20-21", "color": "red"},
                    {"type": "เรือไฟฟ้า", "time": "14:00", "color": "purple"}
                ]
            }
        ]
    }

def get_next_boat(station, flag, direction):
    """คืนค่ารอบเรือถัดไปตามเวลาปัจจุบัน"""
    # โหลดข้อมูลจาก Excel
    file_path = "data/boat_schedule.xlsx"
    df = pd.read_excel(file_path)

    # ตรวจสอบวันปัจจุบัน
    now = get_current_time()
    current_time = now.strftime("%H:%M")
    weekday = now.strftime("%A")

    if weekday == "Saturday":
        day_type = "เสาร์"
    elif weekday == "Sunday":
        day_type = "อาทิตย์"
    else:
        day_type = "จันทร์-ศุกร์"

    default_color = FLAG_COLORS.get(flag, "gray")

    filtered_df = df[
        (df["วัน"] == day_type) & 
        (df["สถานี"] == station) & 
        (df["ธง"] == flag) & 
        (df["ทิศทาง"] == direction)
    ].copy()

    filtered_df["เวลา"] = pd.to_datetime(filtered_df["เวลา"], format="%H:%M:%S", errors="coerce")
    filtered_df = filtered_df.dropna(subset=["เวลา"])
    filtered_df["เวลา"] = filtered_df["เวลา"].dt.strftime("%H:%M")

    next_boats = filtered_df[filtered_df["เวลา"] > current_time]

    if not next_boats.empty:
        next_time = next_boats.iloc[0]["เวลา"]
    else:
        next_time = "ไม่มีรอบถัดไป"

    return {"time": next_time, "color": default_color}

def get_lat_long():
    try:
        response1 = requests.get('https://api.thingspeak.com/channels/2454824/fields/1.json?results=1')
        response2 = requests.get('https://api.thingspeak.com/channels/2454824/fields/2.json?results=1')
        
        response1.raise_for_status()
        response2.raise_for_status()
        
        lat = response1.json().get('feeds')[0].get('field1')
        lng = response2.json().get('feeds')[0].get('field2')

        if lat is None or lng is None:
            raise ValueError("Invalid latitude or longitude from API responses")

        return [{"lat": float(lat), "lng": float(lng)}]

    except (requests.RequestException, ValueError) as e:
        print(f"❌ Error fetching location data: {e}")
        return []

@app.route('/')
def index():
    station = "พระราม7 N24"
    flags = ["ธงส้ม", "ธงเขียวเหลือง", "ธงเหลือง", "ธงเหลืองรอบบ่าย", "ธงแดง", "เรือไฟฟ้า"]
    directions = ["ท่าน้ำนนท์ - ปากเกร็ด", "สาทร - วัดราชสิงขร"]

    schedule = {
        "station": station,
        "time_now": get_current_time().strftime("%H:%M"),
        "routes": []
    }

    for direction in directions:
        route_schedule = []
        for flag in flags:
            boat_data = get_next_boat(station, flag, direction)
            route_schedule.append({
                "type": flag, 
                "time": boat_data["time"], 
                "color": boat_data["color"]
            })
        schedule["routes"].append({"direction": direction, "schedule": route_schedule})

    lat_long_list = get_lat_long()
    current_time = get_current_time().strftime("%H:%M")
    return render_template('map.html', schedule=schedule, lat_long_list=lat_long_list, current_time=current_time)

@app.route('/api/location')
def api_location():
    return jsonify({"locations": get_lat_long()})

def fetch_external_api(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"❌ API Error: {e}")
        return None

@app.route('/dashboard')
def dashboard():
    distance = fetch_external_api("https://api.thingspeak.com/channels/2454824/fields/5.json?results=1")
    time_data = fetch_external_api("https://api.thingspeak.com/channels/2454824/fields/4.json?results=1")
    route = fetch_external_api("https://api.thingspeak.com/channels/2454824/fields/6.json?results=1")
    current_time = get_current_time().strftime("%H:%M")

    data = {
        'distance': float(distance['feeds'][0].get('field5', 999)) if distance and 'feeds' in distance and len(distance['feeds']) > 0 else 999,
        'direction': int(route['feeds'][0].get('field6', 1)) if route and 'feeds' in route and len(route['feeds']) > 0 else 1,
        'time': time_data['feeds'][0].get('field4', '--') if time_data and 'feeds' in time_data and len(time_data['feeds']) > 0 else '--'
    }

    return render_template('dashboard2.html', data=data, current_time=current_time)

if __name__ == '__main__':
    print("🚀 กำลังเริ่มต้น Flask application...")
    app.run(debug=True, port=5000)