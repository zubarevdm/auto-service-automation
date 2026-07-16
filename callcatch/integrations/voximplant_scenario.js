/**
 * Сценарий Voximplant для «Перехвата» — голосовой слой.
 *
 * Что делает: отвечает на переадресованный звонок, здоровается синтезированным
 * голосом, распознаёт речь клиента и на каждую фразу спрашивает наш сервер
 * (/api/voice/*), что сказать дальше. Таймеры мягкого/жёсткого лимита — здесь,
 * потому что часами владеет телефония.
 *
 * ⚠ ЗАГЛУШКА-ИНТЕГРАЦИЯ: код написан по документации Voximplant и требует
 * прогона в их IDE на живом аккаунте (бесплатный триал есть). Перед запуском:
 *   1. Создать приложение, привязать номер, назначить этот сценарий на входящие.
 *   2. Заменить BACKEND и TOKEN.
 *   3. В настройках приложения включить ASR/TTS-профиль (Yandex или Tinkoff
 *      VoiceKit — русские голоса «живого» качества).
 */
require(Modules.ASR);

const BACKEND = "https://ВАШ-СЕРВЕР";           // publичный адрес callcatch serve
const TOKEN = "СМЕНИТЕ-МЕНЯ";                   // CC_WEBHOOK_TOKEN
const LANG = VoiceList.Yandex.Neural.ru_RU_alena; // «живой» голос ассистентки

const SOFT_WRAP_SEC = 180;                      // держать в синхроне с config.py
const HARD_CAP_SEC = 300;

let call = null;
let sessionId = null;
let startedAt = null;

function api(path, body) {
  return Net.httpRequestAsync(`${BACKEND}${path}?token=${TOKEN}`, {
    method: "POST",
    headers: ["Content-Type: application/json; charset=utf-8"],
    postData: JSON.stringify(body),
  }).then((r) => JSON.parse(r.text));
}

function elapsedSec() {
  return Math.round((Date.now() - startedAt) / 1000);
}

async function speakAndListen(text, hangupAfter) {
  const player = VoxEngine.createTTSPlayer(text, { language: LANG });
  player.sendMediaTo(call);
  await new Promise((resolve) => player.addMarker(-1, resolve));
  if (hangupAfter) {
    await api("/api/voice/end", { session_id: sessionId, duration_sec: elapsedSec() });
    call.hangup();
    return;
  }
  listenOnce();
}

function listenOnce() {
  // жёсткий потолок по времени — страховка на случай молчаливого зависания
  if (elapsedSec() >= HARD_CAP_SEC + 30) {
    api("/api/voice/end", { session_id: sessionId, duration_sec: elapsedSec() })
      .then(() => call.hangup());
    return;
  }
  const asr = VoxEngine.createASR({
    profile: ASRProfileList.Yandex.ru_RU,
    singleUtterance: true,
  });
  call.sendMediaTo(asr);
  asr.addEventListener(ASREvents.Result, async (e) => {
    call.stopMediaTo(asr);
    const res = await api("/api/voice/utterance", {
      session_id: sessionId,
      text: e.text || "",
      elapsed_sec: elapsedSec(),
    });
    await speakAndListen(res.say, res.action === "hangup");
  });
  // клиент молчит 10 секунд — переспрашиваем один раз, потом завершаем
  asr.addEventListener(ASREvents.SpeechCaptured, () => {});
  setTimeout(() => {
    // тишина: вероятен автообзвонщик — вежливо завершаем, SMS уже догонит
    api("/api/voice/end", { session_id: sessionId, duration_sec: elapsedSec() })
      .then(() => call.hangup());
  }, 15000);
}

VoxEngine.addEventListener(AppEvents.CallAlerting, async (e) => {
  call = e.call;
  startedAt = Date.now();
  const phone = e.callerid;

  const res = await api("/api/voice/start", { phone });
  if (res.action === "reject" || !res.session_id) {
    // бюджет дня исчерпан / стоп-лист: не поднимаем трубку,
    // бэкенд уже запустил SMS-догон
    call.reject();
    return;
  }
  sessionId = res.session_id;
  call.answer();
  call.record();                                  // запись разговора (см. договор ПДн)
  await speakAndListen(res.say, res.action === "hangup");
});
