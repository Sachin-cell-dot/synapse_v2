/**
 * Static geographic grouping for Karnataka districts.
 * This is presentation metadata only — it never alters rainfall or model values.
 * Aligns with IMD meteorological divisions used in the verified dataset.
 */

export type KarnatakaRegion =
  | 'Coastal Karnataka'
  | 'North Interior Karnataka'
  | 'South Interior Karnataka'

export const REGIONS: readonly KarnatakaRegion[] = [
  'Coastal Karnataka',
  'North Interior Karnataka',
  'South Interior Karnataka',
] as const

/** District name → region. Keys use the verified dataset spelling. */
export const DISTRICT_REGION: Record<string, KarnatakaRegion> = {
  'Dakshina Kannada': 'Coastal Karnataka',
  Udupi: 'Coastal Karnataka',
  'Uttara Kannada': 'Coastal Karnataka',
  Bagalkote: 'North Interior Karnataka',
  Belagavi: 'North Interior Karnataka',
  Bidar: 'North Interior Karnataka',
  Vijayapura: 'North Interior Karnataka',
  Dharwad: 'North Interior Karnataka',
  Gadag: 'North Interior Karnataka',
  Kalaburagi: 'North Interior Karnataka',
  Haveri: 'North Interior Karnataka',
  Koppal: 'North Interior Karnataka',
  Raichur: 'North Interior Karnataka',
  Yadgir: 'North Interior Karnataka',
  'Bengaluru Rural': 'South Interior Karnataka',
  'Bengaluru Urban': 'South Interior Karnataka',
  Ballari: 'South Interior Karnataka',
  Chamarajanagar: 'South Interior Karnataka',
  Chikkamagaluru: 'South Interior Karnataka',
  Chitradurga: 'South Interior Karnataka',
  Davangere: 'South Interior Karnataka',
  Hassan: 'South Interior Karnataka',
  Kodagu: 'South Interior Karnataka',
  Kolar: 'South Interior Karnataka',
  Mandya: 'South Interior Karnataka',
  Mysuru: 'South Interior Karnataka',
  Ramanagara: 'South Interior Karnataka',
  Shivamogga: 'South Interior Karnataka',
  Tumakuru: 'South Interior Karnataka',
  Chikkaballapura: 'South Interior Karnataka',
  Vijayanagara: 'South Interior Karnataka',
}

function normalize(name: string): string {
  const aliases: Record<string, string> = {
    bagalkot: 'bagalkote',
    davanagere: 'davangere',
    ramnagara: 'ramanagara',
  }
  const key = name.trim().toLowerCase()
  return aliases[key] ?? key
}

export function regionForDistrict(district: string | null | undefined): KarnatakaRegion | null {
  if (!district) return null
  const exact = DISTRICT_REGION[district]
  if (exact) return exact
  const match = Object.entries(DISTRICT_REGION).find(([name]) => normalize(name) === normalize(district))
  return match?.[1] ?? null
}

export function districtInRegion(district: string, region: KarnatakaRegion | 'All'): boolean {
  if (region === 'All') return true
  return regionForDistrict(district) === region
}
