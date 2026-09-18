import time
import logging
from ui_detector import get_active_ui_elements, format_for_llm
from agent_engine.brain import AgentBrain
from agent_engine.actuator import Actuator
from agent_engine.overlay import StatusOverlay
from agent_engine.safety import AgentState, SafetyMonitor, _StateManager

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("IntegrationTest")

def main():
    brain = AgentBrain()
    state_manager = _StateManager()
    safety_monitor = SafetyMonitor(state_manager)
    actuator = Actuator()
    overlay = StatusOverlay(state_manager)
    
    # 1. Kullanıcıdan Komut Al
    print("==================================================")
    print("   UÇTAN UCA CANLI MASAÜSTÜ TESTİ (SES HARİÇ)")
    print("==================================================")
    print("Test etmek istediğin pencereyi (örn. Hesap Makinesi, Not Defteri, Tarayıcı) aç.")
    user_command = input("\nAgent'a ne yaptırmak istiyorsun? (Örn: 'Kapat butonuna tıkla'): ")
    
    print("\n[Hazırlık] Odaklanmak istediğin pencereye geçmen için 3 saniyen var...")
    time.sleep(3)
    
    # 2. UI Ağacını Çıkar (Faz 2)
    logger.info("UI elementleri taranıyor...")
    start_time = time.time()
    elements = get_active_ui_elements()
    logger.info(f"Tarama tamamlandı: {len(elements)} element bulundu ({time.time() - start_time:.2f} sn)")
    
    if not elements:
        logger.error("Tıklanabilir element bulunamadı veya pencere okunamadı.")
        return
        
    elements_text = format_for_llm(elements)
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
    
    # 3. LLM Karar Aşaması (Faz 3)
    logger.info("Qwen 2.5 Coder 7B'ye karar için gönderiliyor...")
    start_time = time.time()
    decision = brain.decide_action(user_command, elements_for_brain)
    logger.info(f"LLM Kararı alındı ({time.time() - start_time:.2f} sn):")
    print(decision)
    
    # 4. Aksiyon (Faz 1)
    if decision.get("action") in ("click", "type"):
        target_id = decision.get("target_id")

        # ID'ye karşılık gelen elementi bul
        target_element = next((e for e in elements if e.id == target_id), None)

        if not target_element:
            logger.error("LLM geçersiz bir ID üretti (Halüsinasyon) veya ID bulunamadı.")
            return

        logger.info(f"Hedef Element: {target_element.name} @ ({target_element.center_x}, {target_element.center_y})")
        
        if decision.get("requires_confirm"):
            confirm = input("\n!!! GÜVENLİK UYARISI !!! Bu riskli bir işlem. Onaylıyor musun? (e/h): ")
            if confirm.lower() != 'e':
                logger.info("İşlem kullanıcı tarafından iptal edildi.")
                return
                
        logger.info("Ajan kontrolü alıyor... (İptal etmek için farenizi oynatın veya ESC'ye basın)")
        overlay.start()
        safety_monitor.start()
        safety_monitor.reset()
        state_manager.state = AgentState.ACTIVE
        
        # Hareketi yap
        try:
            actuator.snap_and_click(target_element.center_x, target_element.center_y)
            if decision.get("action") == "type":
                actuator.type_text(decision.get("text_to_type", ""))
                logger.info("Yazma başarılı.")
            else:
                logger.info("Tıklama başarılı.")
        except Exception as e:
            logger.error(f"Aksiyon sırasında hata: {e}")
        finally:
            time.sleep(0.5) # Çerçevenin söndüğünü görmek için
            state_manager.state = AgentState.IDLE
            safety_monitor.stop()
            overlay.stop()

    else:
        logger.info("Ajan 'none' (hiçbir şey yapma) kararı verdi.")

if __name__ == "__main__":
    main()