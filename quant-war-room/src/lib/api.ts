/**
 * api.ts — FastAPI bridge istemcisi
 * Tüm API çağrıları buradan geçer.
 */

const BASE = "/api/v1";

export async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    next: { revalidate: 30 }, // 30s ISR cache
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ─── Types ──────────────────────────────────────────────────────────────────

export interface Signal {
  symbol        : string;
  fiyat         : number;
  final_sinyal  : "LONG" | "SHORT" | "HOLD";
  guven         : "YÜKSEK" | "ORTA" | "DÜŞÜK";
  guven_skoru   : number;
  toplam_skor   : number;
  catisma       : boolean;
  aciklama      : string;
  stop_loss     : number | null;
  take_profit   : number | null;
  risk_odül     : string | null;
  sl_tipi       : string | null;
  atr_kullanildi: boolean;
  atr_degeri    : number | null;
  poz_buyukluk  : number;
  katmanlar     : {
    teknik    : { sinyal: string; puan: number; skor: number };
    sentiment : { skor: number; yorum: string };
    efsane    : { konsensus: string; long_oran: number; short_oran: number; skor: number };
    insider   : { skor: number; carpan: number; yorum: string };
    swan      : { risk_skoru: number; risk_adi: string; risk_off: boolean; vix: number; swan_carpan: number };
    gamma     : { skor: number; piyasa_modu: string; net_gex: number; put_wall: number | null; call_wall: number | null; uoa_var: boolean; pozisyon_ayar: number; tp_ayar: string };
  };
}

export interface SignalsResponse {
  tarih    : string;
  versiyon : string;
  agirliklar: Record<string, number>;
  kararlar : Signal[];
  ozet     : { toplam: number; long: number; short: number; hold: number; catisma: number };
}

export interface RiskResponse {
  tarih          : string;
  portfoy_degeri : number;
  risk_skoru     : number;
  risk_off       : boolean;
  risk_adi       : string;
  tavsiye        : string;
  vix            : { deger: number; risk_seviyesi: string; beklenen_kayip: number };
  kriz_stres     : Record<string, { kayip_pct: number; kalan: number }>;
  en_kotu_kriz   : string;
  monte_carlo    : {
    median_sonuc   : number;
    iflas_olasiligi: number;
    hedef_olasiligi: number;
    var_95         : number;
    cvar_95        : number;
    en_kotu_10     : number;
    en_iyi_10      : number;
  };
  aksiyon: string;
}

export interface Position {
  symbol         : string;
  qty            : number;
  avg_entry      : number;
  current_price  : number;
  market_value   : number;
  unrealized_pl  : number;
  unrealized_plpc: number;
  side           : string;
}

export interface PortfolioResponse {
  timestamp     : string;
  equity        : number;
  cash          : number;
  buying_power  : number;
  portfolio_value: number;
  day_pl        : number;
  day_pl_pct    : number;
  pozisyonlar   : Position[];
  acik_pozisyon : number;
}

export interface AgentStatus {
  dosya          : string;
  mevcut         : boolean;
  son_guncelleme : string | null;
  boyut_kb       : number;
}

export interface AgentsResponse {
  timestamp    : string;
  ajanlar      : Record<string, AgentStatus>;
  aktif_sayisi : number;
  toplam_ajan  : number;
}

// ─── API Fonksiyonlar ────────────────────────────────────────────────────────

export const getSignals    = () => fetchJSON<SignalsResponse>("/signals");
export const getRisk       = () => fetchJSON<RiskResponse>("/risk");
export const getPortfolio  = () => fetchJSON<PortfolioResponse>("/portfolio");
export const getInsider    = () => fetchJSON<Record<string, unknown>>("/insider");
export const getGamma      = () => fetchJSON<Record<string, unknown>>("/gamma");
export const getSentiment  = () => fetchJSON<Record<string, unknown>>("/sentiment");
export const getAgents     = () => fetchJSON<AgentsResponse>("/agents");
export const getHealth     = () => fetchJSON<Record<string, unknown>>("/health");

export async function emergencyStop(secret: string, reason: string) {
  const res = await fetch(`${BASE}/emergency-stop`, {
    method : "POST",
    headers: { "Content-Type": "application/json" },
    body   : JSON.stringify({ confirm: true, secret, reason }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}
