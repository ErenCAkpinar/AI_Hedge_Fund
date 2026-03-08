import yfinance as yf
import pandas as pd

# CEO'nun Kusursuz Hedef Listesi (Şimdilik test için 4 tanesini ekledik)
# Not: USD/JPY paritesi artık ETF üzerinden 'FXY' ile izleniyor (eski spot parite sembolü).
hedef_listesi = [
    "NVDA", "AVGO", "SOXX", "PLTR", "MSTR", "IBIT", 
    "ASTS", "VST", "LMT", "LLY", "TSLA", "GLD", "FXY"
]
print("🤖 Sistem Uyanıyor... Piyasa Verileri Çekiliyor...\n")

for sembol in hedef_listesi:
    try:
        varlik = yf.Ticker(sembol)
        # Sadece son günün kapanış verisini alıyoruz
        veri = varlik.history(period="1d")
        
        if not veri.empty:
            son_fiyat = veri['Close'].iloc[-1]
            print(f"✅ Hedef: {sembol} | Son Fiyat: {son_fiyat:.2f}")
        else:
            print(f"❌ {sembol} için veri bulunamadı!")
            
    except Exception as e:
        print(f"HATA - {sembol}: {e}")

print("\n🦅 Veri Çekimi Tamamlandı. Emrinizi Bekliyorum Kaptan!")