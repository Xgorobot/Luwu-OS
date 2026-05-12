#pragma once
#include <QObject>
#include <QKeyEvent>
#include <functional>

// Keyboard event filter: bridges gpio-keys kernel driver → Qt key events
class KeyFilter : public QObject {
public:
    explicit KeyFilter(QObject *parent = nullptr) : QObject(parent) {}
    std::function<void(QKeyEvent*)> onKey;

    bool blocked = false;  // when true, onKey forwards keys to child app FIFO

protected:
    bool eventFilter(QObject *obj, QEvent *ev) override {
        if (ev->type() == QEvent::KeyPress) {
            auto *ke = static_cast<QKeyEvent*>(ev);
            if (onKey) onKey(ke);
            return true;  // always consume; onKey decides what to do
        }
        return QObject::eventFilter(obj, ev);
    }
};
