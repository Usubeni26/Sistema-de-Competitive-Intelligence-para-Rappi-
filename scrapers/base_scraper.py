import asyncio
import logging
import os
import random
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class BaseScraper(ABC):


    def __init__(
        self,
        platform_name: str,
        headless: bool = True,
        screenshot_enabled: bool = True,
        screenshots_dir: str = "screenshots",
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        timeout_ms: int = 30000,
        max_retries: int = 3,
    ) -> None:
        self.platform_name = platform_name
        self.headless = headless
        self.screenshot_enabled = screenshot_enabled
        self.screenshots_dir = Path(screenshots_dir)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries

        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self.logger = self._setup_logger()
        self._ensure_directories()

    def _setup_logger(self) -> logging.Logger:
        """
        Configura un logger por plataforma.
        """
        logger = logging.getLogger(self.platform_name)

        if not logger.handlers:
            logger.setLevel(logging.INFO)

            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)

            logger.addHandler(console_handler)

        return logger

    def _ensure_directories(self) -> None:
        """
        Crea carpetas necesarias si no existen.
        """
        if self.screenshot_enabled:
            self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """
        Inicia Playwright, navegador, contexto y página.
        """
        self.logger.info(f"[{self.platform_name}] Iniciando navegador...")

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        self.context = await self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="es-MX",
            timezone_id="America/Mexico_City",
        )

        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)

        self.logger.info(f"[{self.platform_name}] Navegador listo.")

    async def close(self) -> None:
        """
        Cierra recursos de Playwright.
        """
        self.logger.info(f"[{self.platform_name}] Cerrando navegador...")

        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            self.logger.warning(f"[{self.platform_name}] Error al cerrar recursos: {e}")

    async def random_delay(self, min_seconds: Optional[float] = None, max_seconds: Optional[float] = None) -> None:
        """
        Espera aleatoria para evitar patrón robótico.
        Si se pasan min_seconds y max_seconds, se usan esos valores; de lo contrario,
        se usan self.min_delay y self.max_delay.
        """
        min_delay = min_seconds if min_seconds is not None else self.min_delay
        max_delay = max_seconds if max_seconds is not None else self.max_delay
        delay = random.uniform(min_delay, max_delay)
        self.logger.info(f"[{self.platform_name}] Esperando {delay:.2f}s...")
        await asyncio.sleep(delay)

    async def safe_goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """
        Navega a una URL con logs y delay.
        """
        if not self.page:
            raise RuntimeError("La página no está inicializada. Llama start() primero.")

        self.logger.info(f"[{self.platform_name}] Navegando a: {url}")
        await self.page.goto(url, wait_until=wait_until)
        await self.random_delay()

    async def save_screenshot(self, name: str) -> Optional[str]:
        """
        Guarda screenshot si está habilitado.
        """
        if not self.screenshot_enabled or not self.page:
            return None

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.platform_name}_{name}_{timestamp}.png"
        filepath = self.screenshots_dir / filename

        try:
            await self.page.screenshot(path=str(filepath), full_page=True)
            self.logger.info(f"[{self.platform_name}] Screenshot guardado: {filepath}")
            return str(filepath)
        except Exception as e:
            self.logger.warning(f"[{self.platform_name}] No se pudo guardar screenshot: {e}")
            return None

    async def click_if_exists(self, selector: str) -> bool:
        """
        Hace click si el selector existe.
        """
        if not self.page:
            return False

        try:
            element = await self.page.query_selector(selector)
            if element:
                await element.click()
                await self.random_delay()
                self.logger.info(f"[{self.platform_name}] Click realizado en {selector}")
                return True
        except Exception as e:
            self.logger.warning(f"[{self.platform_name}] Error en click {selector}: {e}")

        return False

    async def get_text(self, selector: str) -> Optional[str]:
        """
        Obtiene texto de un selector.
        """
        if not self.page:
            return None

        try:
            element = await self.page.query_selector(selector)
            if element:
                text = await element.inner_text()
                return text.strip()
        except Exception as e:
            self.logger.warning(f"[{self.platform_name}] Error obteniendo texto {selector}: {e}")

        return None

    async def get_all_texts(self, selector: str) -> List[str]:
        """
        Obtiene lista de textos para múltiples elementos.
        """
        if not self.page:
            return []

        texts = []
        try:
            elements = await self.page.query_selector_all(selector)
            for el in elements:
                try:
                    txt = await el.inner_text()
                    texts.append(txt.strip())
                except Exception:
                    continue
        except Exception as e:
            self.logger.warning(f"[{self.platform_name}] Error obteniendo lista {selector}: {e}")

        return texts

    async def wait_for_selector_safe(self, selector: str, timeout: Optional[int] = None) -> bool:
        """
        Espera por un selector sin romper el flujo.
        """
        if not self.page:
            return False

        try:
            await self.page.wait_for_selector(selector, timeout=timeout or self.timeout_ms)
            return True
        except Exception:
            return False

    async def retry(self, coro_func, *args, **kwargs) -> Any:
        """
        Ejecuta una coroutine con reintentos.
        """
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                self.logger.info(
                    f"[{self.platform_name}] Intento {attempt}/{self.max_retries}"
                )
                return await coro_func(*args, **kwargs)

            except Exception as e:
                last_error = e
                self.logger.warning(
                    f"[{self.platform_name}] Falló intento {attempt}/{self.max_retries}: {e}"
                )
                await self.random_delay()

        self.logger.error(
            f"[{self.platform_name}] Todos los intentos fallaron. Último error: {last_error}"
        )
        raise last_error

    def build_result(
        self,
        address_id: str,
        city: str,
        zone_name: str,
        zone_type: str,
        restaurant: str,
        product_name: Optional[str] = None,
        product_price: Optional[float] = None,
        delivery_fee: Optional[float] = None,
        service_fee: Optional[float] = None,
        eta_min: Optional[int] = None,
        eta_max: Optional[int] = None,
        discount_text: Optional[str] = None,
        total_price: Optional[float] = None,
        available: Optional[bool] = None,
        currency: str = "MXN",
        raw_metadata: Optional[Dict[str, Any]] = None,
        screenshot_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Estructura estándar para un resultado.
        """
        return {
            "timestamp_utc": datetime.utcnow().isoformat(),
            "platform": self.platform_name,
            "address_id": address_id,
            "city": city,
            "zone_name": zone_name,
            "zone_type": zone_type,
            "restaurant": restaurant,
            "product_name": product_name,
            "product_price": product_price,
            "delivery_fee": delivery_fee,
            "service_fee": service_fee,
            "eta_min": eta_min,
            "eta_max": eta_max,
            "discount_text": discount_text,
            "total_price": total_price,
            "available": available,
            "currency": currency,
            "screenshot_path": screenshot_path,
            "raw_metadata": raw_metadata or {},
        }

    @abstractmethod
    async def scrape_address(self, address_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Método abstracto que cada scraper implementará.
        Debe devolver una lista de resultados normalizados.
        """
        raise NotImplementedError