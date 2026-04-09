"""

───────────────────
Orquestador principal: ejecuta los 3 scrapers (Rappi, UberEats, DiDi)
para TODAS las direcciones almacenadas en addresses.json.

Uso:
    python run_all_scrapers.py [--config addresses.json] [--scrapers rappi ubereats didi]
                               [--headless] [--output-dir results] [--skip-on-error]
    --config        Ruta al JSON de direcciones (default: addresses.json)
    --scrapers      Subset de scrapers a correr: rappi, ubereats, didi (default: los 3)
    --headless      Correr navegador en modo headless (default: True)
    --output-dir    Directorio donde se guardan resultados (default: results/)
    --skip-on-error Si un scraper falla en una dirección, continúa con la siguiente
                    en lugar de abortar toda la ejecución (default: True)
"""

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# ── Importar los scrapers ─────────────────────────────────────────────────────

from scrapers.rappi_scraper import RappiScraper
from scrapers.UberEats_scraper import UberEats_scraper
from scrapers.didi_scraper import DiDiScraper


# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE LOGGING GLOBAL
# ═════════════════════════════════════════════════════════════════════════════

def setup_root_logger(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Consola
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # Archivo
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    root.addHandler(fh)

    return logging.getLogger("orchestrator")


# ═════════════════════════════════════════════════════════════════════════════
# CARGA DE DIRECCIONES
# ═════════════════════════════════════════════════════════════════════════════

def load_addresses(config_path: str) -> List[Dict[str, Any]]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    addresses = data.get("addresses", [])
    if not addresses:
        raise ValueError(f"No se encontraron direcciones en '{config_path}'")

    return addresses


# ═════════════════════════════════════════════════════════════════════════════
# CAPTURA DE CREDENCIALES (una sola vez por plataforma)
# ═════════════════════════════════════════════════════════════════════════════

def collect_credentials(scrapers_to_run: List[str]) -> Dict[str, Any]:
    """
    Solicita credenciales una única vez al iniciar,
    antes de arrancar cualquier navegador.
    """
    creds: Dict[str, Any] = {}

    # ── DiDi requiere teléfono + contraseña ──────────────────────────────────
    if "didi" in scrapers_to_run:
        print("\n─── Credenciales DiDi Food ──────────────────────────────────")
        creds["didi"] = {
            "phone_prefix": input("  Prefijo telefónico [+52]: ").strip() or "+52",
            "phone_number": input("  Número de teléfono: ").strip(),
            "password":     getpass.getpass("  Contraseña: "),
        }
        print("─────────────────────────────────────────────────────────────\n")

    # ── UberEats requiere email + contraseña ─────────────────────────────────
    # UberEats_scraper ya pide las credenciales en __init__ a través de
    # _ensure_credentials(), pero para mantener consistencia y pedirlas

    if "ubereats" in scrapers_to_run:
        print("\n─── Credenciales UberEats ───────────────────────────────────")
        email    = input("  Correo electrónico: ").strip()
        password = getpass.getpass("  Contraseña:         ")
        print("─────────────────────────────────────────────────────────────\n")

        # Pre-inyectar en la variable de clase para evitar que _ensure_credentials
        # vuelva a preguntar al instanciar el scraper.
        UberEats_scraper._credentials = {"email": email, "password": password}
        creds["ubereats"] = UberEats_scraper._credentials

    # ── Rappi no requiere login en el scraper actual ──────────────────────────
    # (no hay flujo de login en rappi_scraper.py)

    return creds


# ═════════════════════════════════════════════════════════════════════════════
# GUARDAR RESULTADOS
# ═════════════════════════════════════════════════════════════════════════════

def save_results(
    results: List[Dict[str, Any]],
    platform: str,
    output_dir: Path,
) -> None:
    """
    Guarda los resultados en:
    results/JSON/results_<platform>_YYYYMMDD.json
    """

    # 👉 Crear subcarpeta JSON dentro de results
    json_dir = output_dir / "JSON"
    json_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    filepath = json_dir / f"results_{platform}_{today}.json"

    existing: List[Dict[str, Any]] = []
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:
            existing = []

    existing.extend(results)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
def normalize_result(result: Dict[str, Any], platform: str) -> Dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(),
        "empresa": platform.upper(),

        "address_id": result.get("address_id"),

        # 🔁 Normalización clave
        "order": result.get("restaurant") or result.get("order"),

        "tiempo_envio": result.get("tiempo_envio"),
        "costo_envio": result.get("costo_envio"),
        "costo_total": result.get("costo_total"),
        "costo_retail": result.get("costo_retail"),
    }

# ═════════════════════════════════════════════════════════════════════════════
# RUNNER POR SCRAPER
# ═════════════════════════════════════════════════════════════════════════════

async def run_scraper_for_all_addresses(
    scraper_name: str,
    addresses: List[Dict[str, Any]],
    creds: Dict[str, Any],
    headless: bool,
    output_dir: Path,
    skip_on_error: bool,
    logger: logging.Logger,
) -> List[Dict[str, Any]]:

    all_results: List[Dict[str, Any]] = []
    total = len(addresses)

    logger.info(f"{'='*60}")
    logger.info(f"[{scraper_name.upper()}] Iniciando scraping de {total} direcciones")
    logger.info(f"{'='*60}")

    # ── Instanciar scraper ────────────────────────────────────────────────────
    scraper_kwargs = dict(
        headless=headless,
        screenshot_enabled=True,
        screenshots_dir=str(output_dir / "screenshots" / scraper_name),
    )

    if scraper_name == "rappi":
        scraper = RappiScraper(**scraper_kwargs)
    elif scraper_name == "ubereats":
        scraper = UberEats_scraper(**scraper_kwargs)
    elif scraper_name == "didi":
        scraper = DiDiScraper(**scraper_kwargs)
    else:
        raise ValueError(f"Scraper desconocido: '{scraper_name}'")

    # ── Inyectar credenciales de DiDi ────────────────────────────────────────

    didi_creds = creds.get("didi")

    try:
        await scraper.start()

        for idx, address in enumerate(addresses, start=1):
            addr_id = address.get("id", f"addr_{idx}")
            logger.info(
                f"[{scraper_name.upper()}] Dirección {idx}/{total}: {addr_id} — {address.get('zone_name', '')}"
            )

            try:
                if scraper_name == "didi" and didi_creds:
                    results = await _run_didi_with_creds(scraper, address, didi_creds)
                else:
                    results = await scraper.scrape_address(address)

                normalized = [normalize_result(r, scraper_name) for r in results]

                all_results.extend(normalized)
                save_results(normalized, scraper_name, output_dir)
                logger.info(
                    f"[{scraper_name.upper()}] ✅ {addr_id}: {len(results)} resultado(s) guardado(s)"
                )

            except Exception as e:
                logger.error(
                    f"[{scraper_name.upper()}] ❌ Error en {addr_id}: {e}",
                    exc_info=True,
                )
                if not skip_on_error:
                    raise

    finally:
        await scraper.close()

    logger.info(
        f"[{scraper_name.upper()}] Finalizado. Total resultados: {len(all_results)} "
        f"en {total} dirección(es)."
    )
    return all_results


async def _run_didi_with_creds(
    scraper: DiDiScraper,
    address_data: Dict[str, Any],
    creds: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Versión de scrape_address para DiDi que usa las credenciales pre-capturadas
    en lugar de solicitarlas por consola en cada iteración.

    Replica el flujo de DiDiScraper.scrape_address() con las credenciales
    inyectadas directamente, sin llamar a input()/getpass().
    """
    results = []

    address_id = address_data["id"]
    city       = address_data["city"]
    zone_name  = address_data["zone_name"]
    zone_type  = address_data["zone_type"]

    raw_order  = address_data.get("order", "")
    order      = raw_order[0] if isinstance(raw_order, list) else raw_order

    raw_retail = address_data.get("retail", "")
    retail     = raw_retail[0] if isinstance(raw_retail, list) else raw_retail

    scraper.logger.info(f"[didi] order='{order}' | retail='{retail}'")

    await scraper.retry(scraper._open_homepage)
    await scraper.save_screenshot(f"{address_id}_home")

    await scraper._try_set_address(address_data)
    await scraper.save_screenshot(f"{address_id}_after_address")

    try:
        await scraper._try_login(
            creds["phone_prefix"],
            creds["phone_number"],
            creds["password"],
        )
    except RuntimeError as e:
        if "DIDI_RATE_LIMITED" in str(e):
            scraper.logger.error(f"[didi] Rate limited en {address_id}: {e}")
            return results
        raise

    await scraper.save_screenshot(f"{address_id}_after_login")

    if not order:
        scraper.logger.warning(f"[didi] 'order' vacío para {address_id}")
        return results

    try:
        result = await scraper.retry(
            scraper._scrape_restaurant_search_result,
            address_data,
            order,
            retail,
        )
        results.append(result)
    except Exception as e:
        scraper.logger.warning(f"[didi] Error en {address_id}: {e}")
        results.append(
            scraper.build_result(
                address_id=address_id,
                city=city,
                zone_name=zone_name,
                zone_type=zone_type,
                restaurant=order,
                available=False,
                raw_metadata={"error": str(e)},
            )
        )

    return results


# ═════════════════════════════════════════════════════════════════════════════
# RESUMEN FINAL
# ═════════════════════════════════════════════════════════════════════════════

def print_summary(
    summary: Dict[str, Dict[str, Any]],
    logger: logging.Logger,
) -> None:
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║                   RESUMEN DE EJECUCIÓN                  ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    for platform, stats in summary.items():
        status = "✅" if stats["errors"] == 0 else "⚠️ "
        logger.info(
            f"║  {status} {platform.upper():<10} "
            f"resultados: {stats['results']:<5} "

        )



# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

async def main(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    logger = setup_root_logger(output_dir)

    logger.info("Iniciando orquestador de scrapers")
    logger.info(f"  Config:   {args.config}")
    logger.info(f"  Scrapers: {args.scrapers}")
    logger.info(f"  Headless: {args.headless}")
    logger.info(f"  Output:   {output_dir}")

    # ── Cargar direcciones ────────────────────────────────────────────────────
    addresses = load_addresses(args.config)
    logger.info(f"  Direcciones cargadas: {len(addresses)}")

    # ── Capturar credenciales antes de lanzar navegadores ────────────────────
    creds = collect_credentials(args.scrapers)

    # ── Ejecutar scrapers secuencialmente ────────────────────────────────────

    summary: Dict[str, Dict[str, Any]] = {}

    for scraper_name in args.scrapers:
        errors_before = 0
        try:
            results = await run_scraper_for_all_addresses(
                scraper_name=scraper_name,
                addresses=addresses,
                creds=creds,
                headless=args.headless,
                output_dir=output_dir,
                skip_on_error=args.skip_on_error,
                logger=logger,
            )
            summary[scraper_name] = {"results": len(results), "errors": 0}
        except Exception as e:
            logger.error(f"[{scraper_name.upper()}] Error fatal: {e}", exc_info=True)
            summary[scraper_name] = {"results": 0, "errors": 1}
            if not args.skip_on_error:
                break

    print_summary(summary, logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orquestador: corre Rappi, UberEats y DiDi para todas las direcciones del JSON."
    )
    parser.add_argument(
        "--config",
        default="config/addresses.json",
        help="Ruta al JSON de direcciones (default: config/addresses.json)",
    )
    parser.add_argument(
        "--scrapers",
        nargs="+",
        choices=["rappi", "ubereats", "didi"],
        default=["rappi", "ubereats", "didi"],
        help="Scrapers a ejecutar (default: los 3)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Correr en modo headless (default: True)",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Mostrar el navegador (útil para depurar)",
    )
    parser.add_argument(
        "--output-dir",
        default="results",
        help="Directorio de salida para JSONs y logs (default: results/)",
    )
    parser.add_argument(
        "--skip-on-error",
        action="store_true",
        default=True,
        help="Continuar con la siguiente dirección si hay error (default: True)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))