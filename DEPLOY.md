# Деплой бота на VPS (круглосуточная работа)

## 1. Купить VPS

Подойдёт самый дешёвый тариф:
- **1 vCPU, 512 MB RAM, 10 GB SSD** — хватит с запасом
- ОС: **Ubuntu 22.04 / 24.04**
- Провайдеры: Timeweb, FirstVDS, RuVDS, Hetzner (не РФ)

---

## 2. Подключиться к серверу

```bash
ssh root@<IP-сервера>
```

## 3. Установить Python и Git

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git
```

## 4. Склонировать репозиторий

```bash
git clone https://github.com/ansiaabd/yougile_ProjectLead_Bot.git
cd yougile_ProjectLead_Bot
```

## 5. Настроить виртуальное окружение

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Если файла `requirements.txt` нет — создать:

```bash
cat > requirements.txt << 'EOF'
python-telegram-bot>=21.0
httpx>=0.27.0
python-dotenv>=1.0.0
requests>=2.32.0
EOF
pip install -r requirements.txt
```

## 6. Создать .env файлы

Скопировать с локального ПК содержимое:

- `.env` (BOT_TOKEN, ADMIN_ID)
- `yougile/.env` (YOUGILE_API_KEY, YOUGILE_COMPANY_ID)

```bash
nano .env          # вставить содержимое
nano yougile/.env  # вставить содержимое
```

## 7. Запустить бота

```bash
python3 main.py
```

Проверить: бот должен ответить в Telegram на `/start`.

## 8. Настроить автозапуск (systemd)

Создать сервис:

```bash
sudo nano /etc/systemd/system/taskbot.service
```

Содержимое (заменить `/root/yougile_ProjectLead_Bot` на свой путь):

```ini
[Unit]
Description=TaskBot Telegram
After=network.target

[Service]
User=root
WorkingDirectory=/root/yougile_ProjectLead_Bot
ExecStart=/root/yougile_ProjectLead_Bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Включить и запустить:

```bash
sudo systemctl daemon-reload
sudo systemctl enable taskbot
sudo systemctl start taskbot
sudo systemctl status taskbot
```

## 9. Настроить туннель (для вебхуков Yougile → Telegram)

Самый простой вариант — установить `localhost.run` как сервис:

```bash
sudo nano /etc/systemd/system/tunnel.service
```

```ini
[Unit]
Description=SSH Tunnel for TaskBot webhooks
After=network.target
Requires=taskbot.service

[Service]
User=root
ExecStart=/usr/bin/ssh -o StrictHostKeyChecking=no -R 80:localhost:8787 nokey@localhost.run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tunnel
sudo systemctl start tunnel
```

**Проверить URL туннеля:**

```bash
journalctl -u tunnel -n 20 --no-pager
# Найти строчку: "https://<id>.lhr.life tunneled with tls termination"
```

## 10. Обновить вебхуки в Yougile

В Telegram отправить боту:

```
/setup_webhooks https://<полученный-айди>.lhr.life
```

---

## Проверка

После деплоя:
1. `/start` — бот отвечает
2. `/add` — создать задачу
3. Создать задачу в Yougile → приходит уведомление в Telegram
4. Выключить ПК → бот продолжает работать
