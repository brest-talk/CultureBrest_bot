import telebot

# Ваш токен вставляется вот сюда, внутрь кавычек:
TOKEN = '8991667352:AAH8RamfY--K1CSNM62H1bLW8bYYIjc_n_c'
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я бот для Brest_TALK, и я успешно запущен!")

bot.infinity_polling()
