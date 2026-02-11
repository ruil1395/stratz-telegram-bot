import os
import logging
import requests
import json
import csv
import io
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask приложение (для webhook)
app = Flask(__name__)

# Конфигурация из переменных окружения Amvera
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")
STRATZ_API_URL = "https://api.stratz.com/graphql"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # URL от Amvera (например, https://your-app.amvera.io)

# Инициализация бота
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().token(TELEGRAM_TOKEN).build()

# GraphQL запросы
PLAYER_FULL_QUERY = """
query GetPlayerFull($steamId: Long!) {
  player(steamAccountId: $steamId) {
    steamAccountId
    name
    isAnonymous
    seasonRank
    lastMatchDateTime
    matches(request: {take: 20}) {
      id
      didRadiantWin
      durationSeconds
      gameMode
      startDateTime
      players(steamAccountId: $steamId) {
        kills
        deaths
        assists
        isRadiant
        networth
        goldPerMinute
        experiencePerMinute
        hero {
          displayName
        }
      }
    }
  }
}
"""

MATCH_QUERY = """
query GetMatch($matchId: Long!) {
  match(id: $matchId) {
    id
    didRadiantWin
    durationSeconds
    gameMode
    lobbyType
    startDateTime
    radiantKills
    direKills
    players {
      steamAccountId
      name
      kills
      deaths
      assists
      isRadiant
      hero {
        displayName
      }
      networth
      goldPerMinute
      experiencePerMinute
      heroDamage
      towerDamage
    }
  }
}
"""

PRO_PLAYERS_QUERY = """
query {
  players(request: {isPro: true, take: 50}) {
    steamAccountId
    name
    team {
      name
    }
    seasonRank
  }
}
"""

class StratzAPI:
    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    def execute_query(self, query, variables=None):
        payload = {
            "query": query,
            "variables": variables or {}
        }
        
        try:
            response = requests.post(
                STRATZ_API_URL,
                json=payload,
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error: {e}")
            return None

stratz_api = StratzAPI(STRATZ_TOKEN)

def save_json(data):
    """Сохраняет данные в JSON файл в памяти"""
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    return io.BytesIO(json_str.encode('utf-8'))

def matches_to_csv(matches):
    """Конвертирует матчи в CSV"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        'Match ID', 'Date', 'Hero', 'Result', 'Kills', 'Deaths', 'Assists',
        'Networth', 'GPM', 'XPM', 'Duration(min)', 'Game Mode'
    ])
    
    for match in matches:
        player_data = match['players'][0]
        hero = player_data['hero']['displayName']
        is_win = (match['didRadiantWin'] and player_data['isRadiant']) or \
                 (not match['didRadiantWin'] and not player_data['isRadiant'])
        result = 'Win' if is_win else 'Loss'
        
        writer.writerow([
            match['id'],
            match.get('startDateTime', 'N/A'),
            hero,
            result,
            player_data['kills'],
            player_data['deaths'],
            player_data['assists'],
            player_data.get('networth', 0),
            player_data.get('goldPerMinute', 0),
            player_data.get('experiencePerMinute', 0),
            match['durationSeconds'] // 60,
            match['gameMode']
        ])
    
    return io.BytesIO(output.getvalue().encode('utf-8'))

def match_to_csv(match_data):
    """Конвертирует данные матча в CSV"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        'Team', 'Player', 'Hero', 'Kills', 'Deaths', 'Assists',
        'Networth', 'GPM', 'XPM', 'Hero Damage', 'Tower Damage'
    ])
    
    for player in match_data['players']:
        team = 'Radiant' if player['isRadiant'] else 'Dire'
        writer.writerow([
            team,
            player.get('name', 'Anonymous'),
            player['hero']['displayName'],
            player['kills'],
            player['deaths'],
            player['assists'],
            player.get('networth', 0),
            player.get('goldPerMinute', 0),
            player.get('experiencePerMinute', 0),
            player.get('heroDamage', 0),
            player.get('towerDamage', 0)
        ])
    
    return io.BytesIO(output.getvalue().encode('utf-8'))

def pro_players_to_csv(players):
    """Конвертирует список про-игроков в CSV"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Name', 'Steam ID', 'Team', 'Rank'])
    
    for player in players:
        writer.writerow([
            player.get('name', 'Unknown'),
            player['steamAccountId'],
            player.get('team', {}).get('name', 'No Team'),
            player.get('seasonRank', 'N/A')
        ])
    
    return io.BytesIO(output.getvalue().encode('utf-8'))

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👤 Статистика игрока (файл)", callback_data='player_file')],
        [InlineKeyboardButton("🎮 Матч в CSV", callback_data='match_file')],
        [InlineKeyboardButton("🏆 Про-игроки (CSV)", callback_data='pro_players')],
        [InlineKeyboardButton("📋 Помощь", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 *Stratz Dota 2 Bot*\n\n"
        "Получайте данные в формате файлов (JSON/CSV)",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🎮 *Команды для получения файлов:*

/player_json <Steam ID> - Полная статистика в JSON
/player_csv <Steam ID> - История матчей в CSV
/match_csv <Match ID> - Детали матча в CSV
/pro_csv - Список про-игроков в CSV

*Примеры:*
`/player_json 123456789`
`/match_csv 7654321`
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def get_player_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет полную статистику игрока в JSON"""
    if not context.args:
        await update.message.reply_text("❌ Укажите Steam ID: `/player_json 123456789`", parse_mode='Markdown')
        return
    
    steam_id = context.args[0]
    await update.message.reply_text("🔍 Загружаю данные...")
    
    result = stratz_api.execute_query(PLAYER_FULL_QUERY, {"steamId": int(steam_id)})
    
    if not result or 'data' not in result or not result['data']['player']:
        await update.message.reply_text("❌ Игрок не найден")
        return
    
    player_data = result['data']['player']
    player_name = player_data.get('name', 'unknown').replace(' ', '_')
    filename = f"player_{player_name}_{steam_id}_{datetime.now().strftime('%Y%m%d')}.json"
    file_obj = save_json(player_data)
    
    await update.message.reply_document(
        document=InputFile(file_obj, filename=filename),
        caption=f"📊 Полная статистика игрока {player_data.get('name', 'Unknown')}"
    )

async def get_player_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет матчи игрока в CSV"""
    if not context.args:
        await update.message.reply_text("❌ Укажите Steam ID: `/player_csv 123456789`", parse_mode='Markdown')
        return
    
    steam_id = context.args[0]
    await update.message.reply_text("🔍 Загружаю матчи...")
    
    result = stratz_api.execute_query(PLAYER_FULL_QUERY, {"steamId": int(steam_id)})
    
    if not result or 'data' not in result or not result['data']['player']:
        await update.message.reply_text("❌ Игрок не найден")
        return
    
    player_data = result['data']['player']
    matches = player_data.get('matches', [])
    
    if not matches:
        await update.message.reply_text("❌ Нет матчей")
        return
    
    player_name = player_data.get('name', 'unknown').replace(' ', '_')
    filename = f"matches_{player_name}_{steam_id}_{datetime.now().strftime('%Y%m%d')}.csv"
    file_obj = matches_to_csv(matches)
    
    await update.message.reply_document(
        document=InputFile(file_obj, filename=filename),
        caption=f"📈 {len(matches)} матчей игрока {player_data.get('name', 'Unknown')}"
    )

async def get_match_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет данные матча в CSV"""
    if not context.args:
        await update.message.reply_text("❌ Укажите Match ID: `/match_csv 7654321`", parse_mode='Markdown')
        return
    
    match_id = context.args[0]
    await update.message.reply_text("🔍 Загружаю матч...")
    
    result = stratz_api.execute_query(MATCH_QUERY, {"matchId": int(match_id)})
    
    if not result or 'data' not in result or not result['data']['match']:
        await update.message.reply_text("❌ Матч не найден")
        return
    
    match_data = result['data']['match']
    filename = f"match_{match_id}_{datetime.now().strftime('%Y%m%d')}.csv"
    file_obj = match_to_csv(match_data)
    winner = "Radiant" if match_data['didRadiantWin'] else "Dire"
    
    await update.message.reply_document(
        document=InputFile(file_obj, filename=filename),
        caption=f"🎮 Матч #{match_id}\n🏆 Победитель: {winner}\n⏱ {match_data['durationSeconds']//60} минут"
    )

async def get_pro_players_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет список про-игроков в CSV"""
    await update.message.reply_text("🔍 Загружаю про-игроков...")
    
    result = stratz_api.execute_query(PRO_PLAYERS_QUERY)
    
    if not result or 'data' not in result:
        await update.message.reply_text("❌ Ошибка загрузки")
        return
    
    players = result['data']['players']
    
    if not players:
        await update.message.reply_text("❌ Список пуст")
        return
    
    filename = f"pro_players_{datetime.now().strftime('%Y%m%d')}.csv"
    file_obj = pro_players_to_csv(players)
    
    await update.message.reply_document(
        document=InputFile(file_obj, filename=filename),
        caption=f"🏆 {len(players)} про-игроков"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'player_file':
        await query.edit_message_text(
            "Выберите формат:\n"
            "`/player_json <Steam ID>` - JSON с полными данными\n"
            "`/player_csv <Steam ID>` - CSV с матчами",
            parse_mode='Markdown'
        )
    elif query.data == 'match_file':
        await query.edit_message_text(
            "`/match_csv <Match ID>` - Данные матча в CSV",
            parse_mode='Markdown'
        )
    elif query.data == 'pro_players
