import discord
from discord import app_commands
from discord.ext import tasks
import configparser
import logging
import logging.handlers
import asyncio
import aiosqlite
import aiohttp
import json
import re
import random
from table2ascii import table2ascii as t2a, PresetStyle

################ GlOBAL VARIABLES ##################

cases_prices = []

################ LOGGER ##################
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
logging.getLogger('discord.http').setLevel(logging.INFO)

log_handler = logging.handlers.RotatingFileHandler(
    filename='discord.log',
    encoding='utf-8',
    maxBytes=32 * 1024 * 1024, # 32 MiB
    backupCount=5, # Rotate through 5 files
)
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)

################ CONFIG PARSER ##################

config = configparser.ConfigParser()
config.read('settings.ini')

################ DISCORD BOT ##################

MY_GUILD = discord.Object(id=config['DSserver']['GuildID'])

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
    
    async def on_ready(self):
        print('Logged in as {0.user}'.format(client))
    
    async def setup_hook(self) -> None:
        # background tasks
        self.bg_task = self.loop.create_task(self.background_market_price_checker())

        # command tree
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)

    async def background_market_price_checker(self):
        logging.debug('BACKGROUND MARKET PRICE CHECKER -- in task')
        try:
            await self.wait_until_ready()
            cases_jsn = json.loads(open(config['Steam']['CasesFilename'], 'r').read())
            while not self.is_closed():
                logging.debug('BACKGROUND MARKET PRICE CHECKER -- in loop')
                table_body=[]

                for case_obj in cases_jsn['cases']:
                    async with aiohttp.ClientSession() as session:
                        csgo_appid = 730
                        currency = 5
                        case_market_name = case_obj['case_name_market']
                        await asyncio.sleep(0.5) # подождать 0.5 секунд между запросами
                        async with session.get(f"https://steamcommunity.com/market/priceoverview/?appid={csgo_appid}&currency={currency}&market_hash_name={case_market_name}") as resp:
                            if resp.status == 200:
                                resp_jsn = json.loads(await resp.text())
                                if resp_jsn['success'] == True:
                                    table_body.append([case_market_name, resp_jsn['median_price'], resp_jsn['volume']])

                # # Отослать сообщение в канал, или отредактировать предыдущее
                channel = await client.fetch_channel(config['DSserver']['PricesChannelID'])
                # отсортировать
                table_body = sorted(table_body, key=lambda x: float(x[1].split(' ')[0].replace(",", ".")))
                table_output = t2a(
                    header = ['Название', 'Цена', 'Кол-во'],
                    body=table_body,
                    style=PresetStyle.thin_compact
                )
                await channel.send(content=f"```\n{table_output}\n```")

                # sleep for 1 min
                await asyncio.sleep(60)
        except Exception as e:
            logger.exception(e)

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
client = MyClient(intents=intents)

async def get_random_pic_url_from_channel():
    channel = await client.fetch_channel(int(config['DSserver']['RandomPicChannelID']))
    pic_urls = []
    async for message in channel.history(limit=200):
        if message.author.bot:
            return None
        
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type.startswith('image'):
                    pic_urls.append(attachment.proxy_url)
    return random.choice(pic_urls)

def get_random_color() -> discord.Color:
    r, g, b = [random.randint(0, 255) for _ in range(3)]

    return discord.Color.from_rgb(r, g, b)

class MyEmbed(discord.Embed):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    async def custom(self):
        self.color = get_random_color()
        self.set_image(url=await get_random_pic_url_from_channel())
        self.set_footer(text="Самый крутой бот для аналитики дропа!!!", icon_url=client.user.avatar.url)

async def simple_answer_embed(title, text):
    embed_msg = MyEmbed(title=title, description=text)
    await embed_msg.custom()

    return embed_msg

# @client.event
# async def on_ready():
#     print('logged in as {0.user}'.format(client))

# Help command
@client.tree.command()
async def bhelp(interaction: discord.Interaction):
    embed_msg = MyEmbed(title="Команды этого монстра", description="Ты пользуешься самым крутым ботом, который трахает ChatGPT во все щели")
    embed_msg.add_field(name="/bhelp", value="Помощь по командам", inline=False)
    embed_msg.add_field(name="/bversion", value="Показать версию", inline=False)
    embed_msg.add_field(name="/bshow", value="Показать список связанных аккаунтов / создать инстанс своего дискорд аккаунта(для первого раза)", inline=False)
    embed_msg.add_field(name="/badd <steamid64>", value="Связать Steam с твоим Discord аккаунтом", inline=False)
    embed_msg.add_field(name="/bremove <steamid64>", value="Отвязать Steam от твоего Discord аккаунта", inline=False)
    await embed_msg.custom()

    await interaction.response.send_message(embed=embed_msg)

# Version command
@client.tree.command()
async def bversion(interaction: discord.Interaction):
    await interaction.response.send_message(embed=await simple_answer_embed(title=f"Версия {config['Bot']['Version']}", text=f"Этот глупый свин недооценивает всемогущество этого бота"))

# List steam accounts that linked to your discord account
@client.tree.command()
async def bshow(interaction: discord.Interaction):
    try:
        async with aiosqlite.connect(config['DB']['Filename']) as db:
            curs = await db.cursor()
            await curs.execute("""SELECT * FROM main.DiscordAccount WHERE discordid=?""", (interaction.user.id, ))
            res = await curs.fetchall()
            
            if len(res) == 0: # Если в бд нет этого discord id, то создать его
                await curs.execute("""INSERT INTO main.DiscordAccount (discordid) VALUES (?)""", (interaction.user.id, ))
                await db.commit()
                
                await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Ты первый раз ввёл эту команду, я внёс тебя в Базу данных, теперь ты можешь привязывать Steam аккаунты"))
            else:
                # TODO: Получить список привязанных аккаунтов
                await curs.execute("""SELECT * FROM main.SteamAccount WHERE discordid=?""", (interaction.user.id, ))
                res2 = await curs.fetchall()
                linked_steamaccountsids = [] # Список steamid64, привязанных к ДС аккаунту
                for row in res2:
                    linked_steamaccountsids.append(row[1])

                if len(linked_steamaccountsids) == 0: # Значит привязанных аккаунтов ещё нет
                    await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"У тебя ещё нет привязанных Steam аккаунтов"))
                else: # показать привязанные аккаунты
                    embed_msg = MyEmbed(title=f"", description=f"Вот список твоих аккаунтов, крутышка:")
                    embed_msg.set_author(name=f"{interaction.user.name}", icon_url=f"{interaction.user.avatar.url}")
                    profile_name = ""
                    icounter = 1
                    for steamid64 in linked_steamaccountsids:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={config['Steam']['WebAPIKey']}&format=json&steamids={steamid64}") as resp:
                                if resp.status == 200:
                                    acc_jsn = json.loads(await resp.text())
                                    profile_name = acc_jsn['response']['players'][0]['personaname']
                                else:
                                    profile_name = "#"
                        embed_msg.add_field(name=f"{icounter}. {profile_name}", value=f"[{steamid64}](https://steamcommunity.com/profiles/{steamid64})", inline=True)
                        icounter+=1
                    await embed_msg.custom()
                    await interaction.response.send_message(embed=embed_msg)

    except aiosqlite.Error as error:
        logger.error(error)

# Link steamid with discord account by discord id
@client.tree.command()
@app_commands.describe(
    steamid64='steamid64 аккаунта'
)
async def badd(interaction: discord.Interaction, steamid64: str):
    # Перед запросами к БД проверить steamid64 регуляркой на валидность
    if not re.match(r'^\d{17}$', steamid64):
        await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Невалидный steamid64, отклоняю это говно"))
    else:
        try:
            async with aiosqlite.connect(config['DB']['Filename']) as db:
                curs = await db.cursor()
                # Проверить, что в базе существует такой steamid64
                await curs.execute("""SELECT * FROM main.SteamAccount WHERE steamid64=?""", (steamid64, ))
                res = await curs.fetchall()

                if len(res) == 0: # Если такого steamid64 не было найдено
                    await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Сори, но пока что я не могу добавить этот steamid64 на твой аккаунт, потому что мне ещё не приходили дропы с ним"))
                else: # Если в бд уже есть записи с этим Steam аккаунтом
                    for row in res:
                        if row[2] == None: # Если аккаунт Steam не привязан ни к чему
                            # Нужно проверить, если ли в базе данных такой discordid
                            await curs.execute("""SELECT * FROM main.DiscordAccount WHERE discordid=?""", (interaction.user.id, ))
                            res2 = await curs.fetchall()

                            if len(res2) == 0: # аккаунта в БД нет
                                await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Сори, но твоего Discord нет в БД, попробуй сначала использовать комманду для внесения своего Discord в БД"))
                            else:
                                await curs.execute("""UPDATE main.SteamAccount SET discordid=? WHERE id=?""", (interaction.user.id, row[0], ))
                                await db.commit()

                                await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Этот Steam успешно связан с вашим Discord аккаунтом"))
                        else: # Если аккаунт Steam уже привязан к какому-то Discord аккаунту
                            ds_user = await interaction.client.fetch_user(row[2])
                            
                            # Проверить что это аккаунт запросившего
                            if ds_user.id == interaction.user.id: # один и тот же человек
                                await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Этот Steam аккаунт уже привязн к твоему Discord"))
                            else: # Это другой discord account
                                await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Сори, но этот Steam аккаунт уже привязан к другому аккаунту: {ds_user.mention}"))
        
        except aiosqlite.Error as error:
            logger.error(error)


@client.tree.command()
@app_commands.describe(
    steamid64='steamid64 аккаунта'
)
async def bremove(interaction: discord.Interaction, steamid64: str):
    if not re.match(r'^\d{17}$', steamid64):
        await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Невалидный steamid64, отклоняю это говно"))
    else:
        try:
            async with aiosqlite.connect(config['DB']['Filename']) as db:
                curs = await db.cursor()
                # Проверить, что в базе существует такой steamid64, и с привязанным discordid вызвавшего
                await curs.execute("""SELECT * FROM main.SteamAccount WHERE steamid64=? AND discordid=?""", (steamid64, interaction.user.id, ))
                res = await curs.fetchall()

                if len(res) == 0: # Если такого steamid64 не было найдено
                    await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Сори, но я не могу удалить этот steamid64 с твоего аккаунта"))
                else: # Если в бд уже есть записи с этим Steam аккаунтом, привязанным к discord id
                    for row in res:
                        await curs.execute("""UPDATE main.SteamAccount SET discordid=? WHERE id=?""", (None, row[0], ))
                        await db.commit()
                        
                        await interaction.response.send_message(embed=await simple_answer_embed(title=f"", text=f"Аккаунт {row[1]} успешно отвязан"))
        
        except aiosqlite.Error as error:
            logger.error(error)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if isinstance(message.channel, discord.TextChannel) == False:
        return
    if message.author.bot == False:
        return
    if len(message.embeds) != 1:
        return
    
    e = message.embeds[0]
    msg_obj = {
        'author': message.embeds[0].author.name,
        'thumbnail_url': message.embeds[0].thumbnail.url,
        'item_name': message.embeds[0].fields[0].value,
        'price': message.embeds[0].fields[1].value,
        'steamid64': message.embeds[0].fields[2].value
    }

    try:
        async with aiosqlite.connect(config['DB']['Filename']) as db:
            curs = await db.cursor()
            await curs.execute("""SELECT * FROM main.SteamAccount WHERE steamid64=?""", (msg_obj['steamid64'], ))
            res = await curs.fetchall()

            if len(res) == 0: # Добавить в БД новый аккаунт, Если нет этого аккаунта
                await curs.execute("""INSERT INTO main.SteamAccount (steamid64, discordid) VALUES (?, NULL)""", (msg_obj['steamid64'], ))
                await db.commit()

            # Теперь добавить предмет и привязать его к только что созданному SteamAccount'у
            await curs.execute("""INSERT INTO main.Item (name, steamid64) VALUES (?, ?)""", (msg_obj['item_name'], msg_obj['steamid64'], ))
            await db.commit()
    
    except aiosqlite.Error as error:
        logger.error(error)

client.run(token=config['Bot']['Token'], log_handler=None)