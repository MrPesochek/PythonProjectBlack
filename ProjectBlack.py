import os
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncio
import aiohttp
import logging

API_TOKEN = '7741948921:AAEOTurL6xWvh_s_AN4uPrf2IlAh3gjElJg'
ACCUWEATHER_API_KEY = '241QGkWFk7nmAfYRwYLIdMOxy0a8kHgH'
ACCUWEATHER_URL = "https://dataservice.accuweather.com"


# Initialize bot and dispatcher
logging.basicConfig(level=logging.INFO)
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=API_TOKEN)

class WeatherStates(StatesGroup):
    waiting_for_start = State()
    waiting_for_end = State()
    waiting_for_stops = State()
    waiting_for_interval = State()

def create_interval_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="3 дня", callback_data="interval_3")
    builder.button(text="5 дней", callback_data="interval_5")
    builder.button(text="Завершить", callback_data="finish")
    builder.adjust(2, 1)
    return builder.as_markup()

def create_stops_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить остановку", callback_data="add_stop")
    builder.button(text="Завершить", callback_data="finish_stops")
    builder.adjust(1)
    return builder.as_markup()

@dp.message(CommandStart())
async def send_welcome(message: Message):
    await message.reply(
        "Я бот прогноза погоды.\n"
        "Используйте /weather для получения прогноза или /help для справки."
    )

@dp.message(Command("help"))
async def send_help(message: Message):
    help_text = (
        "Доступные команды:\n"
        "/start - Начать работу с ботом\n"
        "/help - Получить справку\n"
        "/weather - Запросить прогноз погоды\n\n"
        "Для получения прогноза укажите:\n"
        "1. Начальную точку маршрута\n"
        "2. Конечную точку маршрута\n"
        "3. Промежуточные остановки (если нужны)\n"
        "4. Временной интервал прогноза"
    )
    await message.reply(help_text)

@dp.message(Command("weather"))
async def weather_start(message: Message, state: FSMContext):
    await state.set_state(WeatherStates.waiting_for_start)
    await message.reply("Укажите начальную точку маршрута (город или координаты):")

@dp.message(WeatherStates.waiting_for_start)
async def process_start_point(message: Message, state: FSMContext):
    await state.update_data(start_point=message.text)
    await state.set_state(WeatherStates.waiting_for_end)
    await message.reply("Укажите конечную точку маршрута:")

@dp.message(WeatherStates.waiting_for_end)
async def process_end_point(message: Message, state: FSMContext):
    await state.update_data(end_point=message.text, stops=[])
    await message.reply("Хотите добавить промежуточные остановки?", reply_markup=create_stops_keyboard())

@dp.callback_query(lambda c: c.data == 'add_stop')
async def process_add_stop(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WeatherStates.waiting_for_stops)
    await callback.answer()
    await callback.message.answer("Укажите промежуточную остановку:")

@dp.message(WeatherStates.waiting_for_stops)
async def process_stop(message: Message, state: FSMContext):
    data = await state.get_data()
    stops = data.get('stops', [])
    stops.append(message.text)
    await state.update_data(stops=stops)
    await message.reply("Остановка добавлена. Хотите добавить еще?", reply_markup=create_stops_keyboard())

@dp.callback_query(lambda c: c.data == 'finish_stops')
async def process_finish_stops(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Выберите интервал прогноза:",
        reply_markup=create_interval_keyboard()
    )

async def get_location_key(city_name, session):
    params = {'apikey': ACCUWEATHER_API_KEY, 'q': city_name}
    async with session.get(f"{ACCUWEATHER_URL}/locations/v1/cities/search", params=params) as response:
        response.raise_for_status()
        data = await response.json()
        if not data:
            raise ValueError(f"Город не найден: {city_name}")
        return {
            'key': data[0]['Key'],
            'lat': data[0]['GeoPosition']['Latitude'],
            'lon': data[0]['GeoPosition']['Longitude']
        }

async def get_forecast(location_key, days, session):
    params = {'apikey': ACCUWEATHER_API_KEY, 'details': 'true', 'metric': 'true'}
    async with session.get(f"{ACCUWEATHER_URL}/forecasts/v1/daily/{days}day/{location_key}", params=params) as response:
        response.raise_for_status()
        data = await response.json()
        return data['DailyForecasts']

@dp.callback_query(lambda c: c.data.startswith('interval_'))
async def process_interval(callback: CallbackQuery, state: FSMContext):
    interval = int(callback.data.split('_')[1])
    data = await state.get_data()
    weather_data = {}
    points = [data['start_point']] + data.get('stops', []) + [data['end_point']]

    async with aiohttp.ClientSession() as session:
        for point in points:
            try:
                location = await get_location_key(point, session)
                forecast_data = await get_forecast(location['key'], interval, session)

                weather_data[point] = {}
                for daily in forecast_data:
                    date = daily['Date'].split('T')[0]
                    weather_data[point][date] = {
                        'temperature': round((daily['Temperature']['Minimum']['Value'] +
                                              daily['Temperature']['Maximum']['Value']) / 2),
                        'wind': round(daily['Day']['Wind']['Speed']['Value']),
                        'precipitation': daily['Day']['RainProbability']
                    }

            except aiohttp.ClientError as e:
                logging.error(f"Ошибка получения данных для {point}: {e}")
                await callback.message.answer(f"Ошибка получения данных для {point}.")
            except ValueError as e:
                await callback.message.answer(f"Локация не найдена: {point}")
            except Exception as e:
                logging.exception(f"Непредвиденная ошибка для {point}: {e}")

    if weather_data:
        await send_weather_forecast(callback.from_user.id, weather_data)
    else:
        await callback.message.answer("Не удалось получить прогноз погоды ни для одной точки маршрута.")

    await callback.answer()
    await state.clear()

async def send_weather_forecast(user_id: int, weather_data: dict):
    forecast_message = "Прогноз погоды для вашего маршрута:\n\n"

    for location, forecasts in weather_data.items():
        forecast_message += f"📍 {location}:\n"
        for date, weather in forecasts.items():
            forecast_message += (
                f"  {date}:\n"
                f"  🌡 Температура: {weather['temperature']}°C\n"
                f"  💨 Ветер: {weather['wind']} м/с\n"
                f"  ☔️ Вероятность осадков: {weather['precipitation']}%\n\n"
            )

    await bot.send_message(user_id, forecast_message)

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
