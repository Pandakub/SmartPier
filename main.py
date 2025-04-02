from flask import Flask, render_template, jsonify
import requests
import datetime
import pytz
import math
import redis
import json

import threading
import subprocess
import time

from flask_socketio import SocketIO
import pandas as pd
import numpy as np


app = Flask(__name__)
FLAG_COLORS = {
    "ธงส้ม": "orange",
    "ธงเขียวเหลือง": "green",
    "ธงเหลือง": "lightyellow",
    "ธงเหลืองกลางวัน": "yellow",
    "ธงแดง": "red",
    "เรือไฟฟ้า": "purple"
}

TIMEZONE = pytz.timezone('Asia/Bangkok')
PORT_LAT = 13.811571
PORT_LON = 100.514851

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

STATUS_EXPIRE = 900  # 15 นาที

def get_current_time():
    return datetime.datetime.now(TIMEZONE)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c * 1000

def fetch_external_api(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and len(data) > 0 and "data" in data[0]:
            return data[0]["data"]
        else:
            print("Warning: Unexpected API response format")
            return None
    except requests.RequestException as e:
        print(f"API Error: {e}")
        return None

def save_boat_memory(boat_id, data):
    r.set(f"boat:{boat_id}", json.dumps(data), ex=STATUS_EXPIRE)

def load_boat_memory(boat_id):
    data = r.get(f"boat:{boat_id}")
    if data:
        return json.loads(data)
    return None

def process_boat_data(api_data):
    if not api_data:
        return []

    display_boats = []
    for boat_id, info in api_data.items():
        if info.get("line") != "UrbanLine":
            continue

        distance = haversine(info["lat"], info["lon"], PORT_LAT, PORT_LON)
        memory = load_boat_memory(boat_id)

        # เรืออยู่นอกระยะ 6500 → ลบข้อมูล
        if distance > 6500:
            r.delete(f"boat:{boat_id}")
            continue

        if not memory:
            if distance <= 6000:
                # จุดแรกที่เข้า 6000 m
                memory = {
                    "lat": info["lat"],
                    "lon": info["lon"],
                    "init_distance": distance,
                    "init_lat": info["lat"],
                    "status": "in_range"
                }
                save_boat_memory(boat_id, memory)
            continue

        # ถ้าเคย mark pass แล้ว → ไม่แสดง
        if memory.get("status") == "pass":
            save_boat_memory(boat_id, memory)
            continue

        # ถ้าอยู่ในระยะ 6000
        if distance <= 6000:
            distance_diff = memory["init_distance"] - distance
            direction = memory.get("direction")

            if not direction:
                if memory["init_lat"] > PORT_LAT and distance_diff >= 1000:
                    direction = "ไป สาทร"
                    memory["direction"] = direction
                elif memory["init_lat"] < PORT_LAT and distance_diff >= 1000:
                    direction = "ไป นนทบุรี"
                    memory["direction"] = direction

            # เช็คว่าเลยท่าแล้วหรือยัง → ถ้าเลย Mark pass
            if direction:
                if (direction == "ไป สาทร" and info["lat"] < PORT_LAT) or (direction == "ไป นนทบุรี" and info["lat"] > PORT_LAT):
                    memory["status"] = "pass"
                    save_boat_memory(boat_id, memory)
                    continue

                est_time_min = round((distance / 1000) / 20 * 60)
                display_boats.append({
                    "id": boat_id,
                    "lat": info["lat"],
                    "lon": info["lon"],
                    "distance": round(distance, 2),
                    "direction": direction,
                    "est_time": est_time_min
                })

            # อัปเดตตำแหน่งล่าสุด
            memory["lat"] = info["lat"]
            memory["lon"] = info["lon"]
            save_boat_memory(boat_id, memory)

    return display_boats
#ส่วนของการแสดงผล ตารางเดินเรือ
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
    flags = ["ธงส้ม", "ธงเขียวเหลือง", "ธงเหลือง", "ธงเหลืองกลางวัน", "ธงแดง", "เรือไฟฟ้า"]
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


@app.route('/dashboard')
def index2():
    api_url = "https://us-central1-smartpier-b8896.cloudfunctions.net/app/boatApi"
    data = fetch_external_api(api_url)
    boats = process_boat_data(data) if data else []
    current_time = get_current_time().strftime("%H:%M")
    return render_template('dashboard2.html', boats=boats, current_time=current_time)

@app.route('/api/get_boat_data')
def get_boat_data():
    api_url = "https://us-central1-smartpier-b8896.cloudfunctions.net/app/boatApi"
    data = fetch_external_api(api_url)
    boats = process_boat_data(data) if data else []
    current_time = get_current_time().strftime("%H:%M")
    return jsonify({
        "current_time": current_time,
        "boats": boats
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)
