#include "face.h"
#include "board.h"

// ---------- geometry ----------
static const int EYE_SPR = 124;            // eye sprite is square
static const int MOUTH_W = 170, MOUTH_H = 60;
static const int EYE_CY = 100;             // eye centre line
static const int MOUTH_CY = 190;
static const int GAZE_RANGE_X = 18, GAZE_RANGE_Y = 12;

static uint16_t eyeCol, mouthInner;

static const char* kNames[EXPR_COUNT] = {
  "neutral", "happy", "excited", "curious", "thinking", "listening",
  "surprised", "suspicious", "annoyed", "sad", "sleepy", "asleep", "love"
};

const char* expressionName(Expression e) { return e < EXPR_COUNT ? kNames[e] : "?"; }

bool expressionFromName(const char* name, Expression& out) {
  for (uint8_t i = 0; i < EXPR_COUNT; i++)
    if (strcasecmp(name, kNames[i]) == 0) { out = (Expression)i; return true; }
  return false;
}

// Resting pose. Every expression is expressed as deltas from this.
static const FaceParams kNeutral = {
  /*eyeW*/62, /*eyeH*/66, /*open*/1, /*lidDroop*/0, /*slantL*/0, /*slantR*/0, /*lowerLid*/0,
  /*gazeX*/0, /*gazeY*/0, /*mouthCurve*/0.15f, /*mouthOpen*/0, /*mouthW*/50, /*spacing*/140
};

FaceParams Face::targetFor(Expression e, float intensity) const {
  FaceParams t = kNeutral;
  switch (e) {
    case EXPR_HAPPY:      t.lowerLid = 0.75f; t.mouthCurve = 1;  t.eyeH = 60; break;
    case EXPR_EXCITED:    t.eyeW = 68; t.eyeH = 76; t.lowerLid = 0.3f; t.mouthCurve = 1; t.mouthOpen = 0.6f; t.mouthW = 56; break;
    case EXPR_CURIOUS:    t.gazeX = 0.45f; t.gazeY = -0.35f; t.eyeH = 72; t.mouthOpen = 0.3f; t.mouthW = 24; t.mouthCurve = 0.2f; break;
    case EXPR_THINKING:   t.gazeX = 0.6f; t.gazeY = -0.75f; t.lidDroop = 0.25f; t.mouthCurve = -0.1f; t.mouthW = 30; break;
    case EXPR_LISTENING:  t.eyeW = 66; t.eyeH = 76; t.mouthCurve = 0.25f; t.mouthOpen = 0.1f; break;
    case EXPR_SURPRISED:  t.eyeW = 72; t.eyeH = 84; t.mouthOpen = 0.85f; t.mouthW = 30; t.mouthCurve = 0; break;
    case EXPR_SUSPICIOUS: t.lidDroop = 0.45f; t.gazeX = -0.5f; t.lidSlantL = t.lidSlantR = 3; t.mouthCurve = -0.3f; t.mouthW = 40; break;
    case EXPR_ANNOYED:    t.lidSlantL = t.lidSlantR = 11; t.lidDroop = 0.3f; t.mouthCurve = -0.7f; t.mouthW = 44; break;
    case EXPR_SAD:        t.lidSlantL = t.lidSlantR = -11; t.lidDroop = 0.25f; t.gazeY = 0.5f; t.mouthCurve = -0.9f; break;
    case EXPR_SLEEPY:     t.lidDroop = 0.6f; t.gazeY = 0.3f; t.mouthCurve = 0; t.mouthOpen = 0.2f; t.mouthW = 34; break;
    case EXPR_ASLEEP:     t.open = 0.06f; t.mouthCurve = 0.2f; t.mouthW = 30; break;
    case EXPR_LOVE:       t.lowerLid = 0.55f; t.eyeH = 72; t.eyeW = 66; t.mouthCurve = 1; t.mouthOpen = 0.25f; break;
    default: break;
  }
  if (intensity < 1.0f) {                   // blend neutral -> target
    float* a = (float*)&t; const float* n = (const float*)&kNeutral;
    for (size_t i = 0; i < sizeof(FaceParams) / sizeof(float); i++) a[i] = n[i] + (a[i] - n[i]) * intensity;
  }
  return t;
}

void Face::applyColor565(uint16_t c) {
  if (c == eyeCol) return;
  eyeCol = c;
  uint8_t r = (c >> 11) << 3, g = ((c >> 5) & 0x3F) << 2, b = (c & 0x1F) << 3;
  mouthInner = tft_.color565(r / 4, g / 4, b / 4);       // mouth interior: a dark version of the eye colour
  dirty_ = true;
}

void Face::setEyeColor(uint32_t rgb) {
  baseRGB_ = rgb;
  curR_ = rgb >> 16; curG_ = (rgb >> 8) & 0xFF; curB_ = rgb & 0xFF;   // jump straight to the new base
  applyColor565(tft_.color565(curR_, curG_, curB_));
}

void Face::setMoodColor(Expression e, uint32_t rgb) { if (e < EXPR_COUNT) mood_[e] = rgb; }

// ---------- lifecycle ----------
Face::Face(TFT_eSPI& tft) : tft_(tft), eyeL_(&tft), eyeR_(&tft), mouth_(&tft) {}

void Face::begin() {
  if (eyeCol == 0) setEyeColor(0xEBE128);   // amber default; the portal can change it
  // default mood tints (portal can override / clear): pink blush, angry red, sad blue, thinking cyan, surprise flash
  mood_[EXPR_LOVE] = 0xFF6AD5; mood_[EXPR_ANNOYED] = 0xFF4A4A; mood_[EXPR_SAD] = 0x4C8DFF;
  mood_[EXPR_THINKING] = 0x4CC9F0; mood_[EXPR_SURPRISED] = 0xFFFFFF; mood_[EXPR_EXCITED] = 0xFFD23F; mood_[EXPR_SUSPICIOUS] = 0xB388FF;
  eyeL_.setColorDepth(16); eyeR_.setColorDepth(16); mouth_.setColorDepth(16);
  if (!eyeL_.createSprite(EYE_SPR, EYE_SPR) || !eyeR_.createSprite(EYE_SPR, EYE_SPR) || !mouth_.createSprite(MOUTH_W, MOUTH_H))
    Serial.println("[face] sprite allocation FAILED");
  cur_ = target_ = kNeutral;
  uint32_t now = millis();
  lastInteraction_ = now; nextBlink_ = now + 1500; nextSaccade_ = now + 2000;
  tft_.fillScreen(TFT_BLACK);
}

void Face::setExpression(Expression e, float intensity, uint32_t holdMs) {
  intensity = constrain(intensity, 0.0f, 1.0f);
  if (holdMs == 0) { base_ = e; baseIntensity_ = intensity; holdUntil_ = 0; }
  else holdUntil_ = millis() + holdMs;
  current_ = e;
  target_ = targetFor(e, intensity);
}

void Face::lookAt(float nx, float ny, uint32_t holdMs) {
  autoGazeX_ = constrain(nx, -1.0f, 1.0f); autoGazeY_ = constrain(ny, -1.0f, 1.0f);
  gazeHoldUntil_ = millis() + holdMs;
  nextSaccade_ = gazeHoldUntil_ + 300;
}

void Face::wake() {
  lastInteraction_ = millis();
  base_ = EXPR_NEUTRAL; baseIntensity_ = 1;
  setExpression(EXPR_SURPRISED, 1, 700);
}

void Face::poke() {
  lastInteraction_ = millis();
  setExpression(EXPR_ANNOYED, 1, 1500);
}

bool Face::hitEye(int16_t sx, int16_t sy) const {
  int lx = SCREEN_W / 2 - (int)(cur_.spacing / 2), rx = SCREEN_W / 2 + (int)(cur_.spacing / 2);
  int r = (int)max(cur_.eyeW, cur_.eyeH) / 2 + 8;
  return (abs(sx - lx) < r && abs(sy - EYE_CY) < r) || (abs(sx - rx) < r && abs(sy - EYE_CY) < r);
}

void Face::touch(int16_t sx, int16_t sy) {
  lastInteraction_ = millis();
  if (asleep() || base_ == EXPR_SLEEPY) { wake(); return; }
  if (hitEye(sx, sy)) { poke(); return; }
  lookAt((sx - SCREEN_W / 2) / (SCREEN_W / 2.0f), (sy - EYE_CY) / (SCREEN_H / 2.0f), 900);
  if (holdUntil_ == 0) setExpression(EXPR_CURIOUS, 0.8f, 900);
}

void Face::easeParams(float k) {
  float* c = (float*)&cur_; const float* t = (const float*)&target_;
  for (size_t i = 0; i < sizeof(FaceParams) / sizeof(float); i++) c[i] += (t[i] - c[i]) * k;
}

// ---------- per-frame ----------
void Face::update() {
  uint32_t now = millis();
  if (now - lastFrame_ < 33) return;          // ~30 fps
  lastFrame_ = now;
  uint32_t t0 = micros();

  // temporary expression expired -> back to resting expression
  if (holdUntil_ && now > holdUntil_) { holdUntil_ = 0; current_ = base_; target_ = targetFor(base_, baseIntensity_); }

  // autonomous drowsiness
  uint32_t idle = now - lastInteraction_;
  if (autoSleepMs_ && holdUntil_ == 0) {
    if (idle > autoSleepMs_ && base_ != EXPR_ASLEEP) setExpression(EXPR_ASLEEP);
    else if (idle > autoSleepMs_ * 7 / 10 && base_ == EXPR_NEUTRAL) setExpression(EXPR_SLEEPY);
  }
  bool sleeping = asleep();

  // blink
  float blink = 1.0f;
  if (!sleeping) {
    if (now >= nextBlink_) { blinkStart_ = now; nextBlink_ = now + (random(100) < 20 ? 450 : random(2200, 6500)); }
    if (blinkStart_) {
      float ph = (now - blinkStart_) / 170.0f;
      if (ph >= 1.0f) blinkStart_ = 0; else blink = max(0.04f, 1.0f - sinf(ph * PI));
    }
  }

  // gaze: wander unless a lookAt is being held
  if (!sleeping && now > gazeHoldUntil_ && now >= nextSaccade_) {
    if (random(100) < 40) { autoGazeX_ = 0; autoGazeY_ = 0; }
    else { autoGazeX_ = random(-60, 61) / 100.0f; autoGazeY_ = random(-40, 41) / 100.0f; }
    nextSaccade_ = now + random(1200, 4200);
  }
  ease(gx_, sleeping ? 0 : autoGazeX_, 0.25f); ease(gy_, sleeping ? 0 : autoGazeY_, 0.25f);

  // breathing bob
  float period = sleeping ? 3600.0f : 2400.0f, amp = sleeping ? 3.0f : 1.5f;
  breath_ = sinf(now / period * TWO_PI) * amp;

  easeParams(sleeping || current_ == EXPR_SURPRISED ? 0.28f : 0.18f);

  // mood tint: ease the eye colour toward the current expression's tint (or back to base)
  uint32_t target = mood_[current_] ? mood_[current_] : baseRGB_;
  float tr = target >> 16, tg = (target >> 8) & 0xFF, tb = target & 0xFF;
  ease(curR_, tr, 0.12f); ease(curG_, tg, 0.12f); ease(curB_, tb, 0.12f);
  applyColor565(tft_.color565((uint8_t)lroundf(curR_), (uint8_t)lroundf(curG_), (uint8_t)lroundf(curB_)));

  FaceParams p = cur_;
  p.gazeX = constrain(p.gazeX + gx_, -1.0f, 1.0f);
  p.gazeY = constrain(p.gazeY + gy_, -1.0f, 1.0f);

  // Re-render sprites only when what we would draw has visibly changed.
  struct Key { int16_t v[10]; } key = {
    (int16_t)lroundf(p.eyeW), (int16_t)lroundf(p.eyeH * p.open * blink), (int16_t)lroundf(p.lidDroop * 40),
    (int16_t)lroundf(p.lidSlantL * 2), (int16_t)lroundf(p.lidSlantR * 2), (int16_t)lroundf(p.lowerLid * 40),
    (int16_t)lroundf(p.gazeX * 20), (int16_t)lroundf(p.gazeY * 20), (int16_t)lroundf(p.mouthCurve * 20 + p.mouthOpen * 400), (int16_t)lroundf(p.mouthW)
  };
  static Key lastKey = {{-1}};
  if (dirty_ || memcmp(&key, &lastKey, sizeof key)) {
    dirty_ = false; lastKey = key;
    renderEye(eyeL_, true, p, blink);
    renderEye(eyeR_, false, p, blink);
    renderMouth(mouth_, p);
  }

  int bob = (int)lroundf(breath_);
  int lx = SCREEN_W / 2 - (int)(p.spacing / 2) - EYE_SPR / 2, rx = SCREEN_W / 2 + (int)(p.spacing / 2) - EYE_SPR / 2;
  eyeL_.pushSprite(lx, EYE_CY - EYE_SPR / 2 + bob);
  eyeR_.pushSprite(rx, EYE_CY - EYE_SPR / 2 + bob);
  mouth_.pushSprite(SCREEN_W / 2 - MOUTH_W / 2, MOUTH_CY - MOUTH_H / 2 + bob);

  if (sleeping) renderSleepZs();
  else if (zsDirty_) { tft_.fillRect(0, 0, SCREEN_W, 40, TFT_BLACK); zsDirty_ = false; }

  uint32_t dt = micros() - t0;
  if (dt > maxFrameUs_) maxFrameUs_ = dt;
}

// ---------- drawing ----------
void Face::renderEye(TFT_eSprite& s, bool left, const FaceParams& p, float blink) {
  s.fillSprite(TFT_BLACK);
  float w = p.eyeW, h = max(4.0f, p.eyeH * p.open * blink);
  float cx = EYE_SPR / 2 + p.gazeX * GAZE_RANGE_X, cy = EYE_SPR / 2 + p.gazeY * GAZE_RANGE_Y;
  int x0 = (int)(cx - w / 2), y0 = (int)(cy - h / 2), iw = (int)w, ih = (int)h;
  int r = (int)(min(w, h) * 0.38f);
  s.fillSmoothRoundRect(x0, y0, iw, ih, r, eyeCol, TFT_BLACK);

  // upper lid: horizontal droop + slant. Inner corner = side nearest the nose.
  float slant = left ? p.lidSlantL : p.lidSlantR;
  float lidBase = y0 + p.lidDroop * h;
  float yInner = lidBase + slant, yOuter = lidBase - slant;
  int xInner = left ? x0 + iw + 2 : x0 - 2, xOuter = left ? x0 - 2 : x0 + iw + 2;
  float yHi = min(yInner, yOuter), yLo = max(yInner, yOuter);
  if (yHi > y0 - 2) s.fillRect(x0 - 2, y0 - 2, iw + 4, (int)(yHi - (y0 - 2)) + 1, TFT_BLACK);
  if (yLo - yHi > 0.5f) {
    int xLow = (yInner > yOuter) ? xInner : xOuter;          // corner that hangs lower
    int xHigh = (yInner > yOuter) ? xOuter : xInner;
    s.fillTriangle(xHigh, (int)yHi, xLow, (int)yLo + 1, xLow, (int)yHi, TFT_BLACK);
  }

  // lower lid: black ellipse rising from below gives a happy squint curve
  if (p.lowerLid > 0.02f) {
    int ry = (int)(h * 0.55f), rx = (int)(w * 0.75f);
    int ecy = (int)(y0 + ih + ry - p.lowerLid * h * 0.6f);
    s.fillEllipse((int)cx, ecy, rx, ry, TFT_BLACK);
  }
}

void Face::renderMouth(TFT_eSprite& s, const FaceParams& p) {
  s.fillSprite(TFT_BLACK);
  float cx = MOUTH_W / 2, cy = MOUTH_H / 2 - p.mouthCurve * 5;
  float amp = p.mouthCurve * 13;
  if (p.mouthOpen > 0.03f) {
    int ry = (int)(p.mouthOpen * 16) + 2, rx = (int)(p.mouthW * 0.32f) + 2;
    int oy = (int)(cy + amp * 0.5f + ry * 0.4f);
    s.fillEllipse((int)cx, oy, rx, ry, eyeCol);
    s.fillEllipse((int)cx, oy, max(1, rx - 4), max(1, ry - 4), mouthInner);
  }
  const int N = 12;
  float px = 0, py = 0;
  for (int i = 0; i <= N; i++) {
    float t = i / (float)N, u = 2 * t - 1;
    float x = cx + u * p.mouthW / 2, y = cy + amp * (1 - u * u);
    if (i) s.drawWideLine(px, py, x, y, 4.0f, eyeCol, TFT_BLACK);
    px = x; py = y;
  }
}

void Face::renderSleepZs() {
  // three "z"s drifting up-right from above the right eye, cycling on a 2.4 s loop
  uint32_t now = millis();
  if (now - lastZ_ < 60) return;
  lastZ_ = now;
  tft_.fillRect(0, 0, SCREEN_W, 40, TFT_BLACK);
  zsDirty_ = true;
  tft_.setTextColor(tft_.color565(170, 160, 120), TFT_BLACK);
  tft_.setTextDatum(MC_DATUM);
  float phase = (now % 2400) / 2400.0f;
  int baseX = SCREEN_W / 2 + (int)(cur_.spacing / 2) + 30;
  for (int i = 0; i < 3; i++) {
    float f = fmodf(phase + i / 3.0f, 1.0f);
    int x = baseX + (int)(f * 40), y = 38 - (int)(f * 30);
    if (y > 6) tft_.drawString("z", x, y, f < 0.5f ? 2 : 4);
  }
}
