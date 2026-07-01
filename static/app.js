const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  models: [],
  history: [],
  selectedFile: null,
  lastResult: null,
  socket: null,
  mediaStream: null,
  audioContext: null,
  analyser: null,
  sourceNode: null,
  processor: null,
  connectingStream: false,
  streaming: false,
  stoppingStream: false,
  recordingStarted: 0,
  timerId: null,
  animationId: null,
  previewUrl: null,
  micRecording: false,
  micStream: null,
  micRecorder: null,
  micChunks: [],
  micStarted: 0,
  micTimerId: null,
  micAnimationId: null,
  micAnalyser: null,
  micSourceNode: null,
  micAudioContext: null,
};

const ui = {
  modelSelect: $("#modelSelect"),
  fileInput: $("#fileInput"),
  dropZone: $("#dropZone"),
  selectedAudio: $("#selectedAudio"),
  selectedName: $("#selectedName"),
  selectedMeta: $("#selectedMeta"),
  transcribeButton: $("#transcribeButton"),
  resultEmpty: $("#resultEmpty"),
  resultLoading: $("#resultLoading"),
  resultContent: $("#resultContent"),
  resultText: $("#resultText"),
  resultActions: $("#resultActions"),
  resultSubtitle: $("#resultSubtitle"),
  historyList: $("#historyList"),
  historyEmpty: $("#historyEmpty"),
  toast: $("#toast"),
  canvas: $("#waveform"),
  micButton: $("#micButton"),
  micStatus: $("#micStatus"),
  micTimer: $("#micTimer"),
  micHint: $("#micHint"),
  micCanvas: $("#micWaveform"),
};

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

function formatBytes(bytes = 0) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatDuration(seconds = 0) {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const remain = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  }).format(date);
}

function toast(message, type = "success") {
  ui.toast.classList.toggle("error", type === "error");
  ui.toast.querySelector("span").textContent = message;
  ui.toast.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ui.toast.classList.remove("show"), 2800);
}

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (_) {
    throw new Error("本地识别服务已停止，请双击 run-web.cmd 重新启动后再试");
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) { /* response is not JSON */ }
    throw new Error(message);
  }
  return response.json();
}

function showSection(target) {
  $$(".page-section").forEach((section) => section.classList.toggle("active-section", section.id === target));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.target === target));
  if (target === "history") renderHistory();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadModels() {
  try {
    state.models = await api("/api/models");
    ui.modelSelect.innerHTML = "";
    if (!state.models.length) {
      ui.modelSelect.innerHTML = '<option value="">未发现可用模型</option>';
      ui.modelSelect.disabled = true;
      toast("未发现模型，请将 Vosk 模型放入 models 目录", "error");
    } else {
      const preferred = localStorage.getItem("vosk-model");
      state.models.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = `${model.name} · ${model.size_label}`;
        option.selected = model.id === preferred;
        ui.modelSelect.appendChild(option);
      });
      if (!ui.modelSelect.value) ui.modelSelect.value = state.models[0].id;
    }
    $("#modelStat").textContent = `${state.models.length} 个`;
    $("#modelCountSide").textContent = `${state.models.length} 个模型可用`;
    updateActionState();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadHistory() {
  try {
    state.history = await api("/api/history");
    updateHistoryStats();
    renderHistory();
  } catch (error) {
    toast(error.message, "error");
  }
}

function updateHistoryStats() {
  const count = state.history.length;
  $("#historyBadge").textContent = count;
  $("#historyStat").textContent = `${count} 条`;
}

function selectAudio(file) {
  if (!file) return;
  if (file.size > 200 * 1024 * 1024) {
    toast("音频文件不能超过 200 MB", "error");
    return;
  }
  state.selectedFile = file;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);
  $("#audioPreview").src = state.previewUrl;
  $("#audioPreviewWrap").classList.remove("hidden");
  ui.selectedName.textContent = file.name;
  ui.selectedMeta.textContent = `${formatBytes(file.size)} · ${file.type || "音频文件"}`;
  ui.selectedAudio.classList.remove("hidden");
  updateActionState();
}

function clearSelectedAudio() {
  state.selectedFile = null;
  $("#audioPreview").pause();
  $("#audioPreview").removeAttribute("src");
  $("#audioPreview").load();
  $("#audioPreviewWrap").classList.add("hidden");
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  ui.fileInput.value = "";
  ui.selectedAudio.classList.add("hidden");
  updateActionState();
}

function updateActionState() {
  ui.transcribeButton.disabled = !state.selectedFile || !ui.modelSelect.value || Boolean(state.isTranscribing);
}

function setResultView(view) {
  ui.resultEmpty.classList.toggle("hidden", view !== "empty");
  ui.resultLoading.classList.toggle("hidden", view !== "loading");
  ui.resultContent.classList.toggle("hidden", view !== "content");
  ui.resultActions.classList.toggle("hidden", view !== "content");
}

async function transcribeSelected() {
  if (!state.selectedFile || !ui.modelSelect.value) return;
  state.isTranscribing = true;
  updateActionState();
  setResultView("loading");
  ui.resultSubtitle.textContent = "正在进行本机识别";
  $("#loadingTitle").textContent = "正在加载模型并识别…";

  const form = new FormData();
  form.append("file", state.selectedFile, state.selectedFile.name);
  form.append("model_id", ui.modelSelect.value);

  try {
    const record = await api("/api/transcribe", { method: "POST", body: form });
    state.lastResult = record;
    state.history = [record, ...state.history.filter((item) => item.id !== record.id)];
    updateHistoryStats();
    showResult(record);
    toast("识别完成，记录已保存在本机");
  } catch (error) {
    setResultView("empty");
    ui.resultSubtitle.textContent = "识别失败";
    toast(error.message, "error");
  } finally {
    state.isTranscribing = false;
    updateActionState();
  }
}

function showResult(record) {
  setResultView("content");
  const text = record.text || "未识别到有效语音。请尝试更清晰的录音或更换模型。";
  ui.resultText.textContent = text;
  ui.resultText.classList.toggle("empty-text", !record.text);
  ui.resultSubtitle.textContent = record.text ? "识别完成" : "未检测到有效语音";
  $("#durationMeta").textContent = formatDuration(record.duration);
  $("#elapsedMeta").textContent = `${record.elapsed.toFixed(2)} 秒`;
  $("#modelMeta").textContent = record.model_name;
  if (record.language) $("#modelMeta").textContent += ` · ${record.language}`;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text || "");
    toast("文本已复制");
  } catch (_) {
    toast("浏览器不允许访问剪贴板", "error");
  }
}

function downloadText(text, filename = "vosk-result.txt") {
  const blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 500);
}

function setInputMode(mode) {
  if (state.streaming && mode !== "record") {
    toast("请先结束当前实时识别", "error");
    return;
  }
  if (state.micRecording && mode !== "mic") {
    toast("请先结束当前录音", "error");
    return;
  }
  $$(".input-tab").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
  $("#fileMode").classList.toggle("active", mode === "file");
  $("#micMode").classList.toggle("active", mode === "mic");
  $("#recordMode").classList.toggle("active", mode === "record");
  ui.transcribeButton.classList.toggle("hidden", mode !== "file");
  if (mode === "record") drawIdleWave();
  if (mode === "mic" && !state.micRecording) drawMicIdleWave();
}

function drawIdleWave() {
  if (state.streaming) return;
  const canvas = ui.canvas;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#c8dad4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let x = 0; x <= canvas.width; x += 5) {
    const envelope = Math.exp(-Math.pow((x - canvas.width / 2) / 220, 2));
    const y = canvas.height / 2 + Math.sin(x * .095) * 11 * envelope;
    x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawLiveWave() {
  const canvas = ui.canvas;
  const ctx = canvas.getContext("2d");
  const values = new Uint8Array(state.analyser.fftSize);
  state.analyser.getByteTimeDomainData(values);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const gradient = ctx.createLinearGradient(0, 0, canvas.width, 0);
  gradient.addColorStop(0, "#6ac9b7");
  gradient.addColorStop(.5, "#087b69");
  gradient.addColorStop(1, "#d98a2b");
  ctx.strokeStyle = gradient;
  ctx.lineWidth = 2.4;
  ctx.beginPath();
  const slice = canvas.width / values.length;
  values.forEach((value, index) => {
    const y = (value / 128) * canvas.height / 2;
    const x = index * slice;
    index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  state.animationId = requestAnimationFrame(drawLiveWave);
}

function float32ToInt16Buffer(samples) {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return buffer;
}

function microphoneErrorMessage(error) {
  if (["NotFoundError", "DevicesNotFoundError"].includes(error.name)) {
    return "Windows 未检测到麦克风输入设备。请连接或启用麦克风后重试";
  }
  if (["NotAllowedError", "PermissionDeniedError"].includes(error.name)) {
    return "麦克风权限被拒绝，请在浏览器地址栏和 Windows 隐私设置中允许麦克风";
  }
  if (["NotReadableError", "TrackStartError"].includes(error.name)) {
    return "麦克风无法读取，可能正被其他程序独占，请关闭占用麦克风的软件后重试";
  }
  if (["OverconstrainedError", "ConstraintNotSatisfiedError"].includes(error.name)) {
    return "麦克风不支持请求的录音参数，请切换其他输入设备";
  }
  return `无法启动实时识别：${error.message || error.name}`;
}

async function toggleRecording() {
  if (state.streaming) {
    stopStreaming();
    return;
  }
  if (state.connectingStream) {
    toast("正在连接实时识别服务，请稍候");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.WebSocket || !(window.AudioContext || window.webkitAudioContext)) {
    toast("当前浏览器不支持实时语音，请使用新版 Edge 或 Chrome", "error");
    return;
  }
  if (!ui.modelSelect.value) {
    toast("请先选择识别模型", "error");
    return;
  }
  const selectedModel = state.models.find((model) => model.id === ui.modelSelect.value);
  if (selectedModel?.streaming === false) {
    toast("Qwen3-ASR 不支持实时流式，请使用「录音」tab", "error");
    setInputMode("mic");
    return;
  }

  try {
    state.connectingStream = true;
    $("#recordStatus").textContent = "正在连接模型";
    $("#recordHint").textContent = "首次加载模型可能需要几秒钟…";
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    await state.audioContext.resume();
    state.analyser = state.audioContext.createAnalyser();
    state.analyser.fftSize = 512;
    state.sourceNode = state.audioContext.createMediaStreamSource(state.mediaStream);
    state.processor = state.audioContext.createScriptProcessor(4096, 1, 1);
    state.sourceNode.connect(state.analyser);
    state.analyser.connect(state.processor);
    state.processor.connect(state.audioContext.destination);

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    state.socket = new WebSocket(`${protocol}//${location.host}/api/stream`);
    state.socket.binaryType = "arraybuffer";
    state.socket.onopen = () => {
      state.socket.send(JSON.stringify({
        model_id: ui.modelSelect.value,
        sample_rate: state.audioContext.sampleRate,
      }));
    };
    state.socket.onmessage = handleStreamMessage;
    state.socket.onerror = () => failStreaming("无法连接实时识别服务，请重新运行 run-web.cmd");
    state.socket.onclose = () => {
      if (state.streaming && !state.stoppingStream) failStreaming("实时识别连接已中断");
    };
  } catch (error) {
    failStreaming(microphoneErrorMessage(error));
  }
}

function handleStreamMessage(event) {
  const message = JSON.parse(event.data);
  if (message.type === "ready") {
    state.connectingStream = false;
    state.streaming = true;
    state.stoppingStream = false;
    state.recordingStarted = Date.now();
    state.timerId = setInterval(updateRecordTimer, 250);
    state.processor.onaudioprocess = (audioEvent) => {
      if (!state.streaming || state.socket?.readyState !== WebSocket.OPEN) return;
      if (state.socket.bufferedAmount > 1024 * 1024) return;
      const samples = audioEvent.inputBuffer.getChannelData(0);
      state.socket.send(float32ToInt16Buffer(samples));
      audioEvent.outputBuffer.getChannelData(0).fill(0);
    };
    $("#recordButton").classList.add("recording");
    $("#recordButton").setAttribute("aria-label", "结束实时识别");
    $("#recordStatus").textContent = "正在实时识别";
    $("#recordHint").textContent = "边说边显示文字，点击方块结束并保存";
    ui.resultSubtitle.textContent = "实时识别中";
    ui.resultText.textContent = "正在聆听…";
    ui.resultText.classList.add("empty-text");
    setResultView("content");
    ui.resultActions.classList.add("hidden");
    drawLiveWave();
    return;
  }
  if (message.type === "partial" || message.type === "result" || message.type === "refined") {
    const liveText = `${message.text || ""}${message.partial || ""}`;
    ui.resultText.textContent = liveText || "正在聆听…";
    ui.resultText.classList.toggle("empty-text", !liveText);
    ui.resultText.scrollTop = ui.resultText.scrollHeight;
    if (message.type === "refined") {
      ui.resultText.classList.add("refining");
      setTimeout(() => ui.resultText.classList.remove("refining"), 400);
    }
    return;
  }
  if (message.type === "complete") {
    const record = message.record;
    state.lastResult = record;
    state.history = [record, ...state.history.filter((item) => item.id !== record.id)];
    updateHistoryStats();
    cleanupStreaming(false);
    showResult(record);
    showStreamAudio(record);
    toast("实时识别已完成并保存");
    return;
  }
  if (message.type === "error") failStreaming(message.message || "实时识别失败");
}

function updateRecordTimer() {
  $("#recordTimer").textContent = formatDuration((Date.now() - state.recordingStarted) / 1000);
}

function stopCapture() {
  clearInterval(state.timerId);
  cancelAnimationFrame(state.animationId);
  if (state.processor) {
    state.processor.onaudioprocess = null;
    state.processor.disconnect();
  }
  state.analyser?.disconnect();
  state.sourceNode?.disconnect();
  state.mediaStream?.getTracks().forEach((track) => track.stop());
  state.audioContext?.close().catch(() => {});
  state.processor = null;
  state.analyser = null;
  state.sourceNode = null;
  state.mediaStream = null;
  state.audioContext = null;
}

function stopStreaming() {
  if (!state.streaming || state.stoppingStream) return;
  state.stoppingStream = true;
  stopCapture();
  $("#recordButton").classList.remove("recording");
  $("#recordStatus").textContent = "正在生成最终结果";
  $("#recordHint").textContent = "正在保存录音和历史记录…";
  ui.resultSubtitle.textContent = "正在整理最终结果";
  if (state.socket?.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ event: "stop" }));
  } else {
    failStreaming("实时识别连接已断开");
  }
}

function showStreamAudio(record) {
  clearSelectedAudio();
  ui.selectedName.textContent = record.source;
  ui.selectedMeta.textContent = `${formatDuration(record.duration)} · WAV 实时录音`;
  ui.selectedAudio.classList.remove("hidden");
  $("#audioPreview").src = record.audio_url;
  $("#audioPreviewWrap").classList.remove("hidden");
}

function cleanupStreaming(resetUi = true) {
  stopCapture();
  const socket = state.socket;
  state.socket = null;
  state.connectingStream = false;
  state.streaming = false;
  state.stoppingStream = false;
  if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
  $("#recordButton").classList.remove("recording");
  $("#recordButton").setAttribute("aria-label", "开始实时识别");
  if (resetUi) {
    $("#recordStatus").textContent = "准备实时识别";
    $("#recordTimer").textContent = "00:00";
    $("#recordHint").textContent = "点击后边说边识别，再次点击结束并保存";
  } else {
    $("#recordStatus").textContent = "实时识别完成";
    $("#recordHint").textContent = "录音已保存，可直接试听或再次识别";
  }
  drawIdleWave();
}

function failStreaming(message) {
  cleanupStreaming();
  setResultView("empty");
  ui.resultSubtitle.textContent = "实时识别失败";
  $("#recordStatus").textContent = "麦克风不可用";
  $("#recordHint").textContent = message;
  toast(message, "error");
}

// ===== Mic recording (MediaRecorder → /api/transcribe) =====

function drawMicIdleWave() {
  if (state.micRecording) return;
  const canvas = ui.micCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#c8dad4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let x = 0; x <= canvas.width; x += 5) {
    const envelope = Math.exp(-Math.pow((x - canvas.width / 2) / 220, 2));
    const y = canvas.height / 2 + Math.sin(x * 0.095) * 11 * envelope;
    x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawMicLiveWave() {
  if (!state.micRecording) return;
  const canvas = ui.micCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const analyser = state.micAnalyser;
  const buf = analyser ? new Uint8Array(analyser.fftSize) : null;
  if (analyser) analyser.getByteTimeDomainData(buf);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const gradient = ctx.createLinearGradient(0, 0, canvas.width, 0);
  gradient.addColorStop(0, "#6ac9b7");
  gradient.addColorStop(0.5, "#087b69");
  gradient.addColorStop(1, "#d98a2b");
  ctx.strokeStyle = gradient;
  ctx.lineWidth = 2.4;
  ctx.beginPath();
  const samples = buf ? buf.length : 0;
  for (let i = 0; i < samples; i++) {
    const x = (i / samples) * canvas.width;
    const y = (buf[i] / 128) * canvas.height / 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
  state.micAnimationId = requestAnimationFrame(drawMicLiveWave);
}

function pickMicMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const mime of candidates) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return "";
}

async function startMicRecording() {
  if (state.micRecording) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    toast("当前浏览器不支持麦克风录音", "error");
    return;
  }
  if (!ui.modelSelect.value) {
    toast("请先选择识别模型", "error");
    return;
  }
  const model = state.models.find((item) => item.id === ui.modelSelect.value);
  if (!model) {
    toast("所选模型不可用", "error");
    return;
  }
  try {
    state.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    toast(`无法访问麦克风：${error.message || error.name}`, "error");
    return;
  }
  const mimeType = pickMicMimeType();
  try {
    state.micRecorder = mimeType
      ? new MediaRecorder(state.micStream, { mimeType })
      : new MediaRecorder(state.micStream);
  } catch (error) {
    toast(`MediaRecorder 初始化失败：${error.message || error.name}`, "error");
    state.micStream.getTracks().forEach((t) => t.stop());
    state.micStream = null;
    return;
  }
  state.micChunks = [];
  state.micRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) state.micChunks.push(event.data);
  };
  state.micRecorder.onstop = () => {
    const blob = new Blob(state.micChunks, { type: state.micRecorder.mimeType || "audio/webm" });
    state.micChunks = [];
    submitMicRecording(blob);
  };
  state.micRecorder.start();
  // Flag must be set before kicking off drawMicLiveWave, which early-returns if false.
  state.micRecording = true;
  state.micStarted = Date.now();
  ui.micButton.classList.add("recording");
  ui.micButton.setAttribute("aria-label", "停止录音");

  // Visualization
  try {
    state.micAudioContext = new (window.AudioContext || window.webkitAudioContext)();
    state.micSourceNode = state.micAudioContext.createMediaStreamSource(state.micStream);
    state.micAnalyser = state.micAudioContext.createAnalyser();
    state.micAnalyser.fftSize = 1024;
    state.micSourceNode.connect(state.micAnalyser);
    drawMicLiveWave();
  } catch (error) {
    console.warn("Mic visualization unavailable:", error);
  }
  ui.micStatus.textContent = "正在录音";
  ui.micHint.textContent = "再次点击结束并使用所选模型识别";
  ui.micTimer.textContent = "00:00";
  state.micTimerId = setInterval(() => {
    ui.micTimer.textContent = formatDuration((Date.now() - state.micStarted) / 1000);
  }, 250);
}

async function stopMicRecording() {
  if (!state.micRecording) return;
  if (state.micRecorder && state.micRecorder.state !== "inactive") {
    state.micRecorder.stop();
  }
  state.micRecording = false;
  cancelAnimationFrame(state.micAnimationId);
  state.micAnimationId = null;
  if (state.micTimerId) {
    clearInterval(state.micTimerId);
    state.micTimerId = null;
  }
  if (state.micStream) {
    state.micStream.getTracks().forEach((t) => t.stop());
    state.micStream = null;
  }
  if (state.micSourceNode) {
    try { state.micSourceNode.disconnect(); } catch (e) {}
    state.micSourceNode = null;
  }
  if (state.micAudioContext) {
    state.micAudioContext.close().catch(() => {});
    state.micAudioContext = null;
  }
  state.micAnalyser = null;
  ui.micButton.classList.remove("recording");
  ui.micButton.setAttribute("aria-label", "开始录音");
  ui.micStatus.textContent = "正在识别";
  ui.micHint.textContent = "已停止录音，正在上传并识别…";
}

async function submitMicRecording(blob) {
  if (!blob || blob.size === 0) {
    toast("录音为空", "error");
    ui.micStatus.textContent = "准备录音";
    ui.micHint.textContent = "点击开始录音，再次点击结束并使用所选模型识别";
    drawMicIdleWave();
    return;
  }
  setResultView("loading");
  $("#loadingTitle").textContent = "正在识别录音…";
  const filename = `mic-${Date.now()}.webm`;
  const form = new FormData();
  form.append("file", blob, filename);
  form.append("model_id", ui.modelSelect.value);
  const started = performance.now();
  try {
    const response = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
    const elapsed = (performance.now() - started) / 1000;
    const record = {
      ...data,
      elapsed: data.elapsed ?? round2(elapsed),
      source: data.source || filename,
      model_id: data.model_id || ui.modelSelect.value,
      model_name: data.model_name || (state.models.find((m) => m.id === ui.modelSelect.value)?.name || ""),
      engine: data.engine || (ui.modelSelect.value === "qwen3-asr-0.6b" ? "qwen" : "vosk"),
      audio_file: data.audio_file || null,
      audio_type: data.audio_type || "audio/webm",
      audio_url: data.audio_url || null,
    };
    state.history = [record, ...state.history.filter((item) => item.id !== record.id)];
    updateHistoryStats();
    state.lastResult = record;
    showResult(record);
    showStreamAudio(record);
    ui.micStatus.textContent = "识别完成";
    ui.micHint.textContent = "可以再次录音或切换其他模式";
    toast("录音识别已完成");
  } catch (error) {
    setResultView("empty");
    ui.resultSubtitle.textContent = "识别失败";
    ui.micStatus.textContent = "识别失败";
    ui.micHint.textContent = error.message || "请重试";
    toast(error.message || "识别失败", "error");
  } finally {
    drawMicIdleWave();
  }
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

function renderHistory() {
  const query = $("#historySearch").value.trim().toLowerCase();
  const records = state.history.filter((item) =>
    !query || `${item.source} ${item.text} ${item.model_name}`.toLowerCase().includes(query)
  );
  ui.historyList.innerHTML = records.map((item) => `
    <article class="history-item" data-id="${escapeHtml(item.id)}">
      <span class="history-file-icon">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h5M9 13h6m-6 4h4"/></svg>
      </span>
      <div class="history-main">
        <div class="history-title-row">
          <strong title="${escapeHtml(item.source)}">${escapeHtml(item.source)}</strong>
          <span>${escapeHtml(formatDate(item.created_at))}</span>
        </div>
        <p class="history-text">${escapeHtml(item.text || "未识别到有效语音")}</p>
        <div class="history-tags">
          <span>${escapeHtml(item.model_name)}</span>
          <span>音频 ${formatDuration(item.duration)}</span>
          <span>耗时 ${Number(item.elapsed).toFixed(2)} 秒</span>
        </div>
      </div>
      <div class="history-actions">
        ${item.audio_url ? `<button data-action="play" title="播放音频">
          <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7Z"/></svg>
        </button>` : ""}
        <button data-action="copy" title="复制文本">
          <svg viewBox="0 0 24 24"><path d="M8 8h11v12H8zM5 16H4V4h11v1"/></svg>
        </button>
        <button data-action="open" title="查看结果">
          <svg viewBox="0 0 24 24"><path d="M3 12s3-5 9-5 9 5 9 5-3 5-9 5-9-5-9-5Z"/><circle cx="12" cy="12" r="2"/></svg>
        </button>
        <button class="delete" data-action="delete" title="删除记录">
          <svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5"/></svg>
        </button>
      </div>
    </article>
  `).join("");
  ui.historyEmpty.classList.toggle("hidden", records.length > 0);
}

async function handleHistoryAction(button) {
  const itemElement = button.closest(".history-item");
  const record = state.history.find((item) => item.id === itemElement?.dataset.id);
  if (!record) return;
  const action = button.dataset.action;
  if (action === "play") {
    const player = $("#historyAudio");
    player.src = record.audio_url;
    $("#historyPlayerName").textContent = record.source;
    $("#historyPlayer").classList.remove("hidden");
    try {
      await player.play();
    } catch (_) {
      toast("音频已载入，请点击播放器开始播放");
    }
  }
  if (action === "copy") copyText(record.text);
  if (action === "open") {
    state.lastResult = record;
    showResult(record);
    showSection("workspace");
  }
  if (action === "delete") {
    try {
      await api(`/api/history/${encodeURIComponent(record.id)}`, { method: "DELETE" });
      state.history = state.history.filter((item) => item.id !== record.id);
      if ($("#historyAudio").src.endsWith(`/api/audio/${record.id}`)) closeHistoryPlayer();
      updateHistoryStats();
      renderHistory();
      toast("记录已删除");
    } catch (error) {
      toast(error.message, "error");
    }
  }
}

function closeHistoryPlayer() {
  const player = $("#historyAudio");
  player.pause();
  player.removeAttribute("src");
  player.load();
  $("#historyPlayer").classList.add("hidden");
}

async function clearAllHistory() {
  if (!state.history.length) return;
  if (!window.confirm("确定删除全部识别历史吗？此操作不能撤销。")) return;
  try {
    await api("/api/history", { method: "DELETE" });
    state.history = [];
    closeHistoryPlayer();
    updateHistoryStats();
    renderHistory();
    toast("历史记录已清空");
  } catch (error) {
    toast(error.message, "error");
  }
}

function exportHistory() {
  if (!state.history.length) {
    toast("暂无历史记录可导出", "error");
    return;
  }
  const blob = new Blob([JSON.stringify(state.history, null, 2)], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `vosk-history-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 500);
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showSection(button.dataset.target)));
  $$(".input-tab").forEach((button) => button.addEventListener("click", () => setInputMode(button.dataset.mode)));
  ui.modelSelect.addEventListener("change", () => {
    localStorage.setItem("vosk-model", ui.modelSelect.value);
    toast(`已切换：${ui.modelSelect.options[ui.modelSelect.selectedIndex].text}`);
    const model = state.models.find((item) => item.id === ui.modelSelect.value);
    if (model?.streaming === false && $("#recordMode").classList.contains("active")) {
      toast("Qwen3-ASR 不支持实时流式，已为你切到「录音」", "info");
      setInputMode("mic");
    }
  });
  ui.micButton?.addEventListener("click", () => {
    if (state.micRecording) stopMicRecording();
    else startMicRecording();
  });
  ui.dropZone.addEventListener("click", () => ui.fileInput.click());
  ui.fileInput.addEventListener("change", () => selectAudio(ui.fileInput.files[0]));
  ["dragenter", "dragover"].forEach((event) => ui.dropZone.addEventListener(event, (e) => {
    e.preventDefault(); ui.dropZone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((event) => ui.dropZone.addEventListener(event, (e) => {
    e.preventDefault(); ui.dropZone.classList.remove("dragging");
  }));
  ui.dropZone.addEventListener("drop", (event) => selectAudio(event.dataTransfer.files[0]));
  $("#removeAudio").addEventListener("click", clearSelectedAudio);
  ui.transcribeButton.addEventListener("click", transcribeSelected);
  $("#recordButton").addEventListener("click", toggleRecording);
  $("#copyResult").addEventListener("click", () => copyText(state.lastResult?.text));
  $("#downloadResult").addEventListener("click", () => downloadText(state.lastResult?.text, `${state.lastResult?.source || "vosk-result"}.txt`));
  $("#historySearch").addEventListener("input", renderHistory);
  ui.historyList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (button) handleHistoryAction(button);
  });
  $("#clearHistory").addEventListener("click", clearAllHistory);
  $("#exportHistory").addEventListener("click", exportHistory);
  $("#closeHistoryPlayer").addEventListener("click", closeHistoryPlayer);
  window.addEventListener("beforeunload", () => {
    cleanupStreaming();
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  });
}

async function init() {
  bindEvents();
  drawIdleWave();
  await Promise.all([loadModels(), loadHistory()]);
}

init();
