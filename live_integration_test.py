import time
import logging
from ui_detector import get_active_ui_elements
from agent_engine.brain import AgentBrain
from agent_engine.actuator import Actuator
from agent_engine.overlay import StatusOverlay
from agent_engine.safety import AgentState, SafetyMonitor, _StateManager
from agent_engine.voice import listen_and_transcribe

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LiveVoiceTest")

def main():
    brain = AgentBrain()
    state_manager = _StateManager()
    safety_monitor = SafetyMonitor(state_manager)
    actuator = Actuator()
    overlay = StatusOverlay(state_manager)
    
    # 1. Kullanıcıdan Komut Al
    print("==================================================")
    print("   UÇTAN UCA CANLI SESLİ MASAÜSTÜ TESTİ")
    print("==================================================")
    print("1. Hesap makinesini ekranda hazır tut.")
    print("2. ENTER tuşuna bastığında mikrofon kaydı başlayacak (Maks 4 saniye).")
    print("3. '9 çarpı 8 eşittir' gibi bir komut ver.\n")
    
    input("Hazırsan mikrofonu açmak için ENTER'a bas...")

    # 1. Sesi Dinle ve Metne Çevir (Faz 4)
    logger.info("Dinleniyor... (Konuşmaya başla)")
    user_command = listen_and_transcribe(duration=4, language="tr")
    logger.info("Anlaşılan Komut: %r", user_command)

    if not user_command or len(user_command.strip()) < 2:
        logger.error("Ses anlaşılamadı.")
        return

    print("\n[Hazırlık] Hesap makinesine tıklayıp odakla! (3 saniyen var)")
    time.sleep(3)
    
    # 2. UI Ağacını Çıkar (Faz 2)
    logger.info("UI elementleri taranıyor...")
    elements = get_active_ui_elements()
    logger.info("Tarama tamamlandı: %d element bulundu.", len(elements))
    
    if not elements:
        logger.error("Tıklanabilir element bulunamadı veya pencere okunamadı.")
        return
        
    elements_for_brain = [
        {
            "id": element.id,
            "name": element.name,
            "control_type": element.control_type,
            "center_x": element.center_x,
            "center_y": element.center_y,
        }
        for element in elements
    ]
    
    # 3. LLM Karar Aşaması (Faz 3 - Çoklu İşlem)
    logger.info("Qwen 2.5 Coder 7B'ye gönderiliyor...")
    start_time = time.time()
    actions = brain.decide_action(user_command, elements_for_brain)
    logger.info(
        "LLM Kararı alındı (%.2f sn): Toplam %d işlem.",
        time.time() - start_time,
        len(actions),
    )
    
    # Güvenlik çerçevesini ve acil durdurma kancalarını aç.
    overlay.start()
    safety_monitor.start()
    safety_monitor.reset()
    state_manager.state = AgentState.ACTIVE

    # 4. Aksiyonları sırayla uygula (Faz 1 & 4)
    try:
        for index, decision in enumerate(actions, 1):
            if state_manager.state == AgentState.INTERRUPTED:
                logger.warning("Kullanıcı müdahalesi algılandı! İşlem dizisi durduruldu.")
                break

            logger.info("--- Adım %d: %s ---", index, decision.get("thought"))
            action = decision.get("action")

            if action == "none":
                logger.info("İşlem yok (none).")
                continue

            target_id = decision.get("target_id")
            target_element = next((e for e in elements if e.id == target_id), None)
            if not target_element:
                logger.warning("ID %s bulunamadı, bu adım atlanıyor.", target_id)
                continue

            if decision.get("requires_confirm"):
                logger.warning("Bu adım güvenlik onayı gerektiriyor! (Test modunda atlanıyor)")
                continue

            actuator.snap_and_click(target_element.center_x, target_element.center_y)
            logger.info("Tıklandı: %s", target_element.name)

            if action == "type":
                text_to_type = decision.get("text_to_type", "")
                actuator.type_text(text_to_type)
                logger.info("Klavyeden yazıldı: %s", text_to_type)

            time.sleep(0.3)
    except Exception as exc:
        logger.error("Uygulama sırasında hata: %s", exc)
    finally:
        time.sleep(0.5)
        state_manager.state = AgentState.IDLE
        safety_monitor.stop()
        overlay.stop()

if __name__ == "__main__":
    main()