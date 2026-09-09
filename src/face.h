// Pixel's face: a parametric expression engine rendered with TFT_eSPI sprites.
// Expressions are parameter targets; the engine eases toward them every frame,
// layers autonomous behaviour (blink, gaze, breathing, sleep) on top, and can be
// driven externally via setExpression(name, intensity, holdMs) - the hook for the AI backend.
#pragma once
#include <Arduino.h>
#include <TFT_eSPI.h>

enum Expression : uint8_t {
  EXPR_NEUTRAL, EXPR_HAPPY, EXPR_EXCITED, EXPR_CURIOUS, EXPR_THINKING, EXPR_LISTENING,
  EXPR_SURPRISED, EXPR_SUSPICIOUS, EXPR_ANNOYED, EXPR_SAD, EXPR_SLEEPY, EXPR_ASLEEP,
  EXPR_LOVE, EXPR_COUNT
};

const char* expressionName(Expression e);
bool expressionFromName(const char* name, Expression& out);

// Everything that describes a face pose. Units: px unless stated.
struct FaceParams {
  float eyeW, eyeH;            // eye box size
  float open;                  // 0..1 vertical openness multiplier (blink drives this too)
  float lidDroop;              // 0..1 upper lid coverage (sleepy, suspicious)
  float lidSlantL, lidSlantR;  // upper-lid tilt; + lowers the INNER corner (angry), - lowers the OUTER (sad)
  float lowerLid;              // 0..1 lower lid pushing up (happy squint)
  float gazeX, gazeY;          // -1..1 eye offset within socket
  float mouthCurve;            // -1 frown .. +1 smile
  float mouthOpen;             // 0..1
  float mouthW;
  float spacing;               // distance between eye centres
};

class Face {
public:
  explicit Face(TFT_eSPI& tft);
  void begin();
  void update();                                   // call as often as possible; renders at ~30 fps

  void setExpression(Expression e, float intensity = 1.0f, uint32_t holdMs = 0);
  Expression expression() const { return current_; }
  void lookAt(float nx, float ny, uint32_t holdMs = 800);   // -1..1 gaze, temporarily overrides autonomous gaze
  void poke();                                      // user touched an eye
  void touch(int16_t sx, int16_t sy);               // user touched screen at pixel coords
  void wake();
  bool asleep() const { return current_ == EXPR_ASLEEP; }
  void setAutoSleep(uint32_t ms) { autoSleepMs_ = ms; }
  void setEyeColor(uint16_t c565);
  bool hitEye(int16_t sx, int16_t sy) const;
  uint32_t takeMaxFrameUs() { uint32_t v = maxFrameUs_; maxFrameUs_ = 0; return v; }

private:
  void renderEye(TFT_eSprite& spr, bool left, const FaceParams& p, float blink);
  void renderMouth(TFT_eSprite& spr, const FaceParams& p);
  void renderSleepZs();
  void ease(float& v, float target, float k) { v += (target - v) * k; }
  void easeParams(float k);
  FaceParams targetFor(Expression e, float intensity) const;

  TFT_eSPI& tft_;
  TFT_eSprite eyeL_, eyeR_, mouth_;
  FaceParams cur_{}, target_{};
  Expression current_ = EXPR_NEUTRAL, base_ = EXPR_NEUTRAL;
  float baseIntensity_ = 1.0f;
  uint32_t holdUntil_ = 0;

  // autonomous behaviour state
  uint32_t lastFrame_ = 0, nextBlink_ = 0, blinkStart_ = 0, nextSaccade_ = 0, gazeHoldUntil_ = 0;
  uint32_t lastInteraction_ = 0, autoSleepMs_ = 45000, lastZ_ = 0;
  float autoGazeX_ = 0, autoGazeY_ = 0, gx_ = 0, gy_ = 0, breath_ = 0;
  bool zsDirty_ = false;
  uint32_t maxFrameUs_ = 0;
  bool dirty_ = true;
};
