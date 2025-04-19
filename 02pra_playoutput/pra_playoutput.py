import asyncio
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv
from google import genai
import pandas as pd

# 載入 .env 文件中的環境變數
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API")

# 設置 API 金鑰
genai.api_key = GEMINI_API_KEY

# 定義穿衣建議的生成函數
def generate_clothing_advice(temperature, feels_like, weather, personal_preference):
    # 根據天氣情況和穿衣偏好生成 prompt
    prompt = (
        f"根據整天的平均溫度 {temperature:.1f}°C、平均體感溫度 {feels_like:.1f}°C "
        f"以及主要天氣狀況 {weather}，結合穿衣偏好 {personal_preference}，"
        "請建議全天適合穿著的服裝。"
    )

    # 使用 genai 生成文本
    client = genai.Client(api_key=genai.api_key)

    response = client.models.generate_content(
        model="gemini-2.0-flash",  # 使用您指定的模型
        contents=prompt
    )

    # 回傳生成的文本內容
    return response.text

def time_str_to_minutes(time_str):
    hours, minutes = map(int, time_str.split(':'))
    return hours * 60 + minutes

def is_time_in_range(time_str, start_time, end_time):
    t = time_str_to_minutes(time_str)
    s = time_str_to_minutes(start_time)
    e = time_str_to_minutes(end_time)
    if s <= e:
        return s <= t <= e
    else:
        return t >= s or t <= e

async def get_weather_data(city, township, date, time_period, start_time=None, end_time=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        await page.goto("https://www.cwa.gov.tw/V8/C/W/week.html")
        await page.wait_for_load_state("networkidle")
        
        # 等待並點擊城市按鈕（依據 <span class="heading_3"> 內容）
        try:
            await page.wait_for_selector('span.heading_3', timeout=60000)
        except Exception as e:
            print("未找到城市按鈕:", e)
            await browser.close()
            return None
        
        city_buttons = await page.locator('span.heading_3').all_text_contents()
        found = False
        for city_name in city_buttons:
            if city in city_name:
                city_button = page.locator(f"span.heading_3:text('{city_name}')").locator('..')
                await city_button.click()
                found = True
                break
        if not found:
            print(f"未找到城市: {city}")
            await browser.close()
            return None
        
        # 等待並選擇鄉鎮
        await page.wait_for_selector('select#TID')
        options_locator = page.locator("select#TID option")
        count = await options_locator.count()
        found_value = None
        for i in range(count):
            option_el = options_locator.nth(i)
            option_text = await option_el.inner_text()
            if township in option_text:
                found_value = await option_el.get_attribute("value")
                break
        if found_value:
            await page.select_option("select#TID", found_value)
        else:
            print(f"未找到鄉鎮: {township}")
        
        await page.locator("button:not([data-gtmtitle]):has-text('確定')").click()
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        
        # 使用正確的表格 id（請確認此 id 與實際網頁一致）
        try:
            await page.wait_for_selector("#TableId3hr", timeout=60000)
        except Exception as e:
            print("等待表格載入超時:", e)
            await browser.close()
            return None
        
        weather_data = {}
        weather_data['date'] = date
        
        if time_period.strip() == "全天":
            # 取得所有時間標籤（跳過第一欄 "時間"）
            header_elements = await page.locator("tr.time th:not(:first-child)").element_handles()
            header_ids = []
            header_times = []
            for element in header_elements:
                t_text = (await element.text_content()).strip()
                header_id = await element.get_attribute("id")
                if t_text and header_id:
                    header_ids.append(header_id)
                    header_times.append(t_text)
            print("全日所有時間標籤：", header_times)
            
            temps = []
            feels = []
            weathers = []
            for hid in header_ids:
                try:
                    sel_temp = f"td[headers*='{hid}'][headers*='PC3_T'] .tem-C"
                    temp_text = await page.locator(sel_temp).inner_text()
                    temps.append(float(temp_text))
                except Exception as e:
                    print(f"抓取溫度失敗 ({hid}):", e)
                try:
                    sel_feels = f"td[headers*='{hid}'][headers*='PC3_AT'] .tem-C"
                    feels_text = await page.locator(sel_feels).inner_text()
                    feels.append(float(feels_text))
                except Exception as e:
                    print(f"抓取體感失敗 ({hid}):", e)
                try:
                    sel_weather = f"td[headers*='{hid}'][headers*='PC3_Wx'] img"
                    w = await page.locator(sel_weather).get_attribute("title")
                    weathers.append(w)
                except Exception as e:
                    print(f"抓取天氣狀況失敗 ({hid}):", e)
            
            if temps and feels:
                avg_temp = sum(temps) / len(temps)
                avg_feels = sum(feels) / len(feels)
            else:
                avg_temp = avg_feels = None
            weather_cond = weathers[0] if weathers else None
            
            weather_data["time_period"] = "全天"
            weather_data["temperature"] = avg_temp
            weather_data["feels_like"] = avg_feels
            weather_data["weather_condition"] = weather_cond
        else:
            try:
                temp_text = await page.locator(f"td[headers='C10017 day{date[-1]}'] .tem-C").inner_text()
                feels_text = await page.locator(f"td[headers='PC3_AT PC3_D1'] .tem-C").inner_text()
                weather_cond = await page.locator(f"td[headers='C10017 day{date[-1]}'] .signal img").get_attribute("title")
                weather_data["time_period"] = time_period
                weather_data["temperature"] = float(temp_text)
                weather_data["feels_like"] = float(feels_text)
                weather_data["weather_condition"] = weather_cond
            except Exception as e:
                print("讀取白天資料失敗:", e)
                weather_data = None
        
        await browser.close()
        return weather_data

async def main():
    city = input("請輸入城市：")
    township = input("請輸入鄉鎮：")
    date = input("請輸入日期 (格式: day1, day2, day3): ")
    
    # 對於全天狀態，直接設定 time_period 為 "全天"
    time_period = "全天"
    
    weather_data = await get_weather_data(city, township, date, time_period)
    if weather_data:
        print(f"日期：{weather_data['date']}, 時間區段：{weather_data['time_period']}")
        print(f"平均溫度：{weather_data['temperature']}°C")
        print(f"平均體感溫度：{weather_data['feels_like']}°C")
        print(f"主要天氣狀況：{weather_data['weather_condition']}")
        
        personal_preference = input("請輸入您的穿衣偏好：")
        clothing_advice = generate_clothing_advice(
            weather_data['temperature'],
            weather_data['feels_like'],
            weather_data['weather_condition'],
            personal_preference
        )
        print(f"穿衣建議：{clothing_advice}")
        
        df = pd.DataFrame([{
            'city': city,
            'township': township,
            'date': weather_data['date'],
            'time_period': weather_data['time_period'],
            'temperature': weather_data['temperature'],
            'feels_like': weather_data['feels_like'],
            'weather_condition': weather_data['weather_condition'],
            'clothing_advice': clothing_advice
        }])
        df.to_csv("weather_clothing_advice.csv", mode='a', header=False, index=False)
        print("對話紀錄已保存到 'weather_clothing_advice.csv'")

if __name__ == "__main__":
    asyncio.run(main())
