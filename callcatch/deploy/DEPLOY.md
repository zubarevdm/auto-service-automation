# Деплой «Перехвата» на VPS (Beget, Ubuntu/Debian)

Зависимостей у приложения нет — нужен только Python 3.10+. Весь деплой:
скопировать код, положить .env, включить systemd-юнит, накрыть nginx с HTTPS.

## 1. Код и окружение

```bash
ssh user@ваш-vps
sudo mkdir -p /opt/callcatch && sudo chown $USER /opt/callcatch
# с локальной машины:
scp -r callcatch/ user@ваш-vps:/opt/callcatch/

cp deploy/env.example /opt/callcatch/.env   # и заполнить ключи
```

## 2. systemd

```bash
sudo cp /opt/callcatch/deploy/callcatch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now callcatch
systemctl status callcatch          # должно быть active (running)
curl http://127.0.0.1:8040/health   # {"ok": true}
```

## 3. nginx + HTTPS

```bash
sudo cp /opt/callcatch/deploy/nginx-callcatch.conf /etc/nginx/sites-available/callcatch
# поправить server_name на ваш домен/поддомен (например, api.вашдомен.ru)
sudo ln -s /etc/nginx/sites-available/callcatch /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.вашдомен.ru      # выпуск сертификата
```

## 4. Проверка снаружи

```bash
curl https://api.вашдомен.ru/health
curl -X POST "https://api.вашдомен.ru/webhook/call?token=ВАШ_ТОКЕН" \
  -H "Content-Type: application/json" \
  -d '{"phone": "+79991112233", "status": "missed"}'
```

Дашборд: https://api.вашдомен.ru/ — он за тем же nginx; если не хотите
светить его в интернет, закройте location / basic-auth'ом (пример в конфиге).

## 5. Бэкапы

База — один файл `/opt/callcatch/data/callcatch.db`. Крон раз в сутки:

```
0 4 * * * cp /opt/callcatch/data/callcatch.db /opt/callcatch/backups/callcatch-$(date +\%F).db
```
