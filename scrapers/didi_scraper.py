import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from scrapers.base_scraper import BaseScraper


class DiDiScraper(BaseScraper):
    """

    Flujo:
      1. Abre DiDi Food y cierra popups
      2. Ingresa la dirección y la confirma
      3. Hace login con prefijo de país (+52), teléfono y contraseña
      4. En la pantalla principal busca el campo `order` en el buscador global
      5. Selecciona el restaurante que coincida textualmente con la búsqueda
      6. Dentro del restaurante, busca el producto en H4/H3 y lo agrega al carrito
     
      9. Persiste los datos en raw_YYYYMMDD.json con la misma estructura que Rappi

    Campos del JSON de salida:
        timestamp, empresa, address_id, order,
        tiempo_envio, costo_envio, costo_total, costo_retail
    """

    def __init__(self, **kwargs):
        super().__init__(platform_name="didi", **kwargs)
        self.base_url = "https://www.didi-food.com/es-MX/food/"



    async def scrape_address(self, address_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Scrapea una dirección. Busca el campo `order` del JSON.
        """
        results: List[Dict[str, Any]] = []

        address_id = address_data["id"]
        city = address_data["city"]
        zone_name = address_data["zone_name"]
        zone_type = address_data["zone_type"]

        # --- NORMALIZACIÓN: order puede ser lista o string ---
        raw_order = address_data.get("order", "")
        if isinstance(raw_order, list):
            order = raw_order[0] if raw_order else ""
            self.logger.info(f"[didi] order era lista, se toma el primer elemento: '{order}'")
        else:
            order = raw_order

        # --- NORMALIZACIÓN: retail puede ser lista o string ---
        raw_retail = address_data.get("retail", "")
        if isinstance(raw_retail, list):
            retail = raw_retail[0] if raw_retail else ""
            self.logger.info(f"[didi] retail era lista, se toma el primer elemento: '{retail}'")
        else:
            retail = raw_retail

        # --- Credenciales de login (se solicitan al iniciar la ejecución) ---
        import getpass
        phone_prefix = input("Prefijo telefónico [+57]: ").strip() or "+57"
        phone_number = input("Número de teléfono: ").strip()
        password = getpass.getpass("Contraseña: ")

        self.logger.info(f"[didi] Iniciando scrape para {address_id} - {zone_name}")
        self.logger.info(f"[didi] order='{order}' | retail='{retail}'")

        await self.retry(self._open_homepage)
        await self.save_screenshot(f"{address_id}_home")

        await self._try_set_address(address_data)
        await self.save_screenshot(f"{address_id}_after_address")

        try:
            await self._try_login(phone_prefix, phone_number, password)
        except RuntimeError as e:
            if "DIDI_RATE_LIMITED" in str(e):
                self.logger.error(
                    f"[didi] Proceso detenido para {address_id}: {e}"
                )
                return results
            raise
        await self.save_screenshot(f"{address_id}_after_login")

        if not order:
            self.logger.warning(f"[didi] El campo 'order' está vacío para {address_id}.")
            return results

        try:
            result = await self.retry(
                self._scrape_restaurant_search_result,
                address_data,
                order,
                retail,
            )
            results.append(result)
        except Exception as e:
            self.logger.warning(f"[didi] Error scrapeando order '{order}' en {address_id}: {e}")
            results.append(
                self.build_result(
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

    # ==========================================================================
    # HOMEPAGE Y POPUPS

    async def _open_homepage(self) -> None:
        # Bloquear permiso de geolocalización a nivel de contexto del navegador
        # antes de navegar, para que el browser no pregunte en absoluto.
        try:
            await self.page.context.grant_permissions([], origin=self.base_url)
        except Exception as e:
            self.logger.debug(f"[didi] grant_permissions no disponible: {e}")

        await self.safe_goto(self.base_url)
        await self.page.wait_for_selector("body", timeout=15000)

        # Cerrar el popup de ubicación del navegador si aparece en pantalla
        await self._dismiss_geolocation_popup()

        await self._dismiss_popups()
        await self.random_delay(1.0, 2.0)

    async def _dismiss_geolocation_popup(self) -> None:
        """
        Cierra el popup nativo/web de permisos de ubicación haciendo clic en
        'No permitir nunca' o variantes equivalentes.
        """
        geo_deny_selectors = [
            "button:has-text('No permitir nunca')",
            "button:has-text('Nunca permitir')",
            "button:has-text('Bloquear')",
            "button:has-text('No permitir')",
            "button:has-text('Deny')",
            "button:has-text('Block')",
            "button:has-text('Never allow')",
            "[data-testid*='deny']",
            "[data-testid*='block']",
            "[aria-label*='No permitir']",
            "[aria-label*='Bloquear']",
        ]

        for selector in geo_deny_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=3000)
                    self.logger.info(f"[didi] Popup de ubicación cerrado con: {selector}")
                    await self.random_delay(0.5, 1.0)
                    return
            except Exception:
                continue

        self.logger.debug("[didi] No se detectó popup de ubicación en pantalla.")

    async def _dismiss_popups(self) -> None:
        possible_buttons = [
            "button:has-text('Aceptar')",
            "button:has-text('Aceptar cookies')",
            "button:has-text('Entendido')",
            "button:has-text('Continuar')",
            "button:has-text('Cerrar')",
            "button:has-text('Ahora no')",
            "button:has-text('No, gracias')",
            "button[aria-label='close']",
            "button[aria-label='Close']",
            "[data-testid='close-button']",
            "[class*='close']",
            "[class*='dismiss']",
        ]
        for selector in possible_buttons:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=2000)
                    self.logger.info(f"[didi] Popup cerrado con selector: {selector}")
                    await self.random_delay(0.5, 1.0)
            except Exception:
                continue

    # ==========================================================================
    # SET ADDRESS
    # ==========================================================================

    async def _try_set_address(self, address_data: Dict[str, Any]) -> bool:

        if not self.page:
            return False

        address = address_data["address"]
        self.logger.info(f"[didi] Intentando setear dirección: {address}")

        # ── 1. Localizar el input de dirección ──────────────────────────────────
        input_selectors = [
            "input[placeholder='Ingresar dirección de entrega']",
            "input.el-input__inner",
            "input[placeholder*='dirección de entrega']",
            "input[placeholder*='dirección']",
            "input[placeholder*='Dirección']",
            "input[placeholder*='¿Dónde']",
            "input[placeholder*='Ingresa tu dirección']",
            "input[placeholder*='ubicación']",
            "input[autocomplete='off'][type='text']",
            "input[type='text']",
        ]

        input_element = None
        for selector in input_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    input_element = locator
                    self.logger.info(f"[didi] Input de dirección encontrado: {selector}")
                    break
            except Exception:
                continue

        if not input_element:
            self.logger.error("[didi] No se encontró input de dirección.")
            return False

        # ── 2. Escribir la dirección ─────────────────────────────────────────────
        try:
            await input_element.scroll_into_view_if_needed()
            await input_element.click()
            await input_element.fill("")
            await input_element.type(address, delay=80)
            self.logger.info(f"[didi] Dirección escrita: '{address}'")
        except Exception as e:
            self.logger.error(f"[didi] Error al escribir dirección: {e}")
            return False

        await self.random_delay(1.5, 2.5)

        # ── 3. Clic en "Buscar comida" ───────────────────────────────────────────
        confirm_selectors = [
            "button:has-text('Buscar comida')",
            "button.el-find_btn",
            "button:has-text('Buscar')",
            "button:has-text('Confirmar')",
            "button:has-text('Continuar')",
            "button:has-text('Siguiente')",
            "button[type='submit']",
        ]

        confirmed = False
        for selector in confirm_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.scroll_into_view_if_needed()
                    await locator.click(timeout=3000)
                    self.logger.info(f"[didi] Botón 'Buscar comida' clicado: {selector}")
                    confirmed = True
                    break
            except Exception:
                continue

        if not confirmed:
            self.logger.warning("[didi] No se encontró botón 'Buscar comida'.")

        await self.random_delay(2.0, 3.0)
        self.logger.info("[didi] Seteo de dirección finalizado.")
        return True

    # ==========================================================================
    # LOGIN
    # ==========================================================================

    async def _try_login(
        self,
        phone_prefix: str,
        phone_number: str,
        password: str,
    ) -> bool:
        """
        Realiza el flujo de login de DiDi Food:
          1. Selecciona el prefijo de país (ej. +52)
          2. Ingresa el número de teléfono
          3. Ingresa la contraseña
          4. Confirma el login
        Retorna True si se completó el flujo, False si falló o el login ya estaba activo.
        """
        if not self.page:
            return False

        # Si ya hay sesión activa (existe buscador o home), no reloguear
        already_logged = await self._is_logged_in()
        if already_logged:
            self.logger.info("[didi] Sesión ya activa, se omite login.")
            return True

        self.logger.info("[didi] Iniciando flujo de login...")
        await self.save_screenshot("login_before")

        # ── 1. Selector de prefijo de país ───────────────────────────────────────
   
        prefix_selectors = [
            "span.country-code",
            "[class*='country-code']",
            f"span:has-text('+52')",  # valor por defecto MX que hay que cambiar, se sobreescribe solo ingresando el valor
            f"span:has-text('+')",
            "[class*='prefix']",
        ]

        prefix_clicked = False
        for selector in prefix_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=3000)
                    self.logger.info(f"[didi] Prefijo clicado con: {selector}")
                    prefix_clicked = True
                    await self.random_delay(0.8, 1.5)
                    break
            except Exception as e:
                self.logger.debug(f"[didi] Falló selector de prefijo '{selector}': {e}")

        if prefix_clicked:
            # Buscar la opción +57 Colombia en el dropdown
            option_selectors = [
                f"li:has-text('{phone_prefix}')",
                f"[role='option']:has-text('{phone_prefix}')",
                f"div:has-text('{phone_prefix}')",
                f"span:has-text('{phone_prefix}')",
            ]
            for selector in option_selectors:
                try:
                    option = self.page.locator(selector).first
                    if await option.count() > 0 and await option.is_visible():
                        await option.click(timeout=3000)
                        self.logger.info(f"[didi] Prefijo '{phone_prefix}' seleccionado")
                        await self.random_delay(0.5, 1.0)
                        break
                except Exception as e:
                    self.logger.debug(f"[didi] Falló opción de prefijo '{selector}': {e}")

        # ── 2. Campo de teléfono ─────────────────────────────────────────────────

        phone_selectors = [
            "div.phone-number input",
            "div.phone-number input.input-default",
            "input[placeholder='Número de teléfono']",
            "input[placeholder*='teléfono']",
            "input[placeholder*='telefono']",
            "input[placeholder*='celular']",
            "input[placeholder*='phone']",
            "input[type='tel']",
            "input.input-default",
        ]

        phone_input = None
        for selector in phone_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    phone_input = locator
                    self.logger.info(f"[didi] Campo de teléfono encontrado: {selector}")
                    break
            except Exception:
                continue

        if not phone_input:
            self.logger.error("[didi] No se encontró campo de teléfono para login.")
            return False

        try:
            await phone_input.scroll_into_view_if_needed()
            await phone_input.click(click_count=3)  # selecciona todo el texto existente
            await self.random_delay(0.2, 0.4)
            await phone_input.press("Control+a")
            await phone_input.press("Backspace")
            await phone_input.fill("")
            await self.random_delay(0.2, 0.3)
            await phone_input.press_sequentially(phone_number, delay=100)
            await self.random_delay(0.3, 0.5)
            value = await phone_input.input_value()
            self.logger.info(f"[didi] Teléfono escrito: '{value}'")
            # Verificar que se escribió correctamente; si no, reintentar con fill()
            if value != phone_number:
                self.logger.warning(
                    f"[didi] Valor del campo ('{value}') difiere del esperado "
                    f"('{phone_number}'). Reintentando con fill()."
                )
                await phone_input.fill(phone_number)
                await self.random_delay(0.2, 0.3)
                value = await phone_input.input_value()
                self.logger.info(f"[didi] Teléfono tras fill(): '{value}'")
        except Exception as e:
            self.logger.error(f"[didi] Error escribiendo teléfono: {e}")
            return False

        await self.random_delay(0.5, 1.0)

        # ── 3. Campo de contraseña ───────────────────────────────────────────────

        password_selectors = [
            "div.input-pwd input",
            "input[placeholder='Ingresa tu contraseña']",
            "input[placeholder*='contraseña']",
            "input[placeholder*='password']",
            "div.input-pwd input.input-default",
            "input[type='password']",
        ]

        password_input = None
        for selector in password_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    password_input = locator
                    self.logger.info(f"[didi] Campo de contraseña encontrado: {selector}")
                    break
            except Exception:
                continue

        if not password_input:
            self.logger.error("[didi] No se encontró campo de contraseña para login.")
            return False

        try:
            await password_input.scroll_into_view_if_needed()
            await password_input.click(click_count=3)  # selecciona todo el texto existente
            await self.random_delay(0.2, 0.4)
            await password_input.press("Control+a")
            await password_input.press("Backspace")
            await password_input.fill("")
            await self.random_delay(0.2, 0.3)
            await password_input.press_sequentially(password, delay=100)
            await self.random_delay(0.3, 0.5)
            self.logger.info("[didi] Contraseña escrita.")
        except Exception as e:
            self.logger.error(f"[didi] Error escribiendo contraseña: {e}")
            return False

        await self.random_delay(0.5, 1.0)

        # ── 4. Aceptar términos y condiciones ────────────────────────────────────
        terms_selectors = [
            "span.checkbox.check-default",
            "span[class*='checkbox'][class*='check-default']",
            "span.check-default",
            "[class*='check-default']",
        ]

        terms_clicked = False
        for selector in terms_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.scroll_into_view_if_needed()
                    await locator.click(timeout=3000)
                    self.logger.info(f"[didi] Términos aceptados con: {selector}")
                    terms_clicked = True
                    await self.random_delay(0.5, 1.0)
                    break
            except Exception:
                continue

        if not terms_clicked:
            self.logger.warning("[didi] No se encontró checkbox de términos, continuando de todas formas.")

        await self.random_delay(0.5, 1.0)

        # ── 5. Botón de confirmar login ──────────────────────────────────────────
        login_button_selectors = [
            "div.button.actived",
            "div.button-wrap div.button",
            "button:has-text('Iniciar sesión')",
            "button:has-text('Ingresar')",
            "button:has-text('Entrar')",
            "button:has-text('Continuar')",
            "button:has-text('Login')",
            "button:has-text('Sign in')",
            "button[type='submit']",
        ]

        login_clicked = False
        for selector in login_button_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.scroll_into_view_if_needed()
                    await locator.click(timeout=3000)
                    self.logger.info(f"[didi] Botón de login clicado: {selector}")
                    login_clicked = True
                    break
            except Exception:
                continue

        if not login_clicked:
            self.logger.warning("[didi] No se encontró botón de login; intentando Enter.")
            try:
                await password_input.press("Enter")
                login_clicked = True
            except Exception:
                pass
        await self.random_delay(3.0, 5.0)

        # ── 6. Detectar error de demasiados intentos ─────────────────────────────
        rate_limit_detected = await self._is_rate_limited()
        if rate_limit_detected:
            self.logger.error(
                "[didi] Demasiados intentos de sesión. DiDi bloqueó el acceso temporalmente. "
                "Intenta de nuevo más tarde."
            )
            await self.save_screenshot("login_rate_limited")
            raise RuntimeError("DIDI_RATE_LIMITED: demasiados intentos de sesión")

        # ── 7. Cerrar posibles popups post-login ─────────────────────────────────
        await self._dismiss_popups()
        await self.save_screenshot("login_after")

        logged_in = await self._is_logged_in()
        if logged_in:
            self.logger.info("[didi] Login exitoso.")
        else:
            self.logger.warning("[didi] No se pudo confirmar login exitoso.")

        return logged_in

    async def _is_logged_in(self) -> bool:

        if not self.page:
            return False
        logged_in_indicators = [
            "input[placeholder*='Buscar']",
            "input[placeholder*='buscar']",
            "input[type='search']",
            "[data-testid*='search']",
            "[class*='home']",
            "[class*='Home']",
            "[aria-label*='perfil']",
            "[aria-label*='cuenta']",
        ]
        for selector in logged_in_indicators:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False


    async def _is_rate_limited(self) -> bool:

        if not self.page:
            return False

        rate_limit_phrases = [
            "demasiados intentos",
            "too many attempts",
            "intenta más tarde",
            "intenta de nuevo más tarde",
            "inténtalo más tarde",
            "try again later",
            "account temporarily",
            "cuenta bloqueada",
            "temporalmente bloqueado",
            "has excedido",
            "límite de intentos",
        ]

        try:
            body_text = (await self.page.locator("body").inner_text()).lower()
            for phrase in rate_limit_phrases:
                if phrase in body_text:
                    self.logger.error(f"[didi] Frase de bloqueo detectada: '{phrase}'")
                    return True
        except Exception as e:
            self.logger.debug(f"[didi] Error verificando rate limit: {e}")

        return False

    # ==========================================================================
    # BÚSQUEDA DE RESTAURANTE
    # ==========================================================================

    async def _try_search_restaurant(self, query: str) -> bool:

        if not self.page:
            return False

        query = str(query).strip()
        if not query:
            return False

        search_input_selectors = [
            "input[placeholder*='Buscar']",
            "input[placeholder*='buscar']",
            "input[placeholder*='¿Qué']",
            "input[placeholder*='Escribe']",
            "input[placeholder*='Ingresa']",
            "input[type='search']",
            "input[type='text']",
            "[data-testid*='search'] input",
        ]

        input_locator = None
        for selector in search_input_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.scroll_into_view_if_needed()
                    await locator.click()
                    await locator.fill("")
                    await locator.type(query, delay=50)
                    await self.random_delay(1.2, 2.0)
                    input_locator = locator
                    self.logger.info(f"[didi] Búsqueda escrita: '{query}' con selector: {selector}")
                    break
            except Exception as e:
                self.logger.debug(f"[didi] Falló selector de búsqueda '{selector}': {e}")

        if not input_locator:
            self.logger.warning("[didi] No se encontró buscador global.")
            return False

        await self.random_delay(1.5, 2.5)

        selected = await self._select_search_result(query)
        if selected:
            self.logger.info(f"[didi] Restaurante seleccionado para '{query}'")
            await self.random_delay(2.0, 3.0)
            return True

        # Fallback con Enter
        self.logger.warning(f"[didi] Sin coincidencia visible para '{query}'. Usando Enter.")
        try:
            await input_locator.press("Enter")
            await self.random_delay(2.0, 3.0)
            selected = await self._select_search_result(query)
            if selected:
                self.logger.info(f"[didi] Restaurante seleccionado tras Enter para '{query}'")
                await self.random_delay(2.0, 3.0)
                return True
        except Exception as e:
            self.logger.warning(f"[didi] Error haciendo Enter en búsqueda: {e}")

        return False

    async def _select_search_result(self, query: str) -> bool:

        if not self.page:
            return False

        normalized_query = self._normalize_text(query)

        # Intento con get_by_text exacto
        try:
            exact_locator = self.page.get_by_text(query, exact=True).first
            if await exact_locator.count() > 0 and await exact_locator.is_visible():
                self.logger.info(f"[didi] get_by_text exact encontró '{query}'")
                try:
                    await exact_locator.click(timeout=4000)
                    return True
                except Exception:
                    clickable = exact_locator.locator(
                        "xpath=ancestor::a[1] | xpath=ancestor::*[@role='link'][1] | xpath=ancestor::button[1]"
                    ).first
                    if await clickable.count() > 0:
                        await clickable.click(timeout=4000)
                        return True
        except Exception as e:
            self.logger.debug(f"[didi] get_by_text exact falló: {e}")

        result_selectors = [
            "a[href*='/store/']",
            "a[href*='/restaurant/']",
            "a[href*='/tienda/']",
            "[data-testid*='store'] a",
            "[data-testid*='restaurant'] a",
            "[data-testid*='store']",
            "[data-testid*='restaurant']",
            "[class*='store']",
            "[class*='restaurant']",
            "[role='link']",
            "a",
        ]

        await self.random_delay(1.0, 2.0)

        exact_match = None
        partial_match = None

        for selector in result_selectors:
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                if count == 0:
                    continue

                for i in range(count):
                    el = locator.nth(i)
                    if not await el.is_visible():
                        continue
                    text = (await el.inner_text()).strip()
                    if not text:
                        continue
                    normalized_text = self._normalize_text(text)

                    if normalized_text == normalized_query:
                        exact_match = el
                        self.logger.info(f"[didi] Coincidencia EXACTA: '{text}'")
                        break
                    if normalized_query in normalized_text and partial_match is None:
                        partial_match = el
                        self.logger.info(f"[didi] Coincidencia PARCIAL: '{text}'")

                if exact_match:
                    break
            except Exception:
                continue

        target = exact_match or partial_match

        if target:
            try:
                await target.scroll_into_view_if_needed()
                await target.click(timeout=5000)
                self.logger.info("[didi] Click en resultado seleccionado.")
                return True
            except Exception:
                try:
                    await target.evaluate("el => el.click()")
                    return True
                except Exception as e2:
                    self.logger.warning(f"[didi] Falló JS click: {e2}")

        self.logger.warning(f"[didi] No se encontró resultado para '{query}'")
        return False



    async def _add_product_by_h4(self, product_name: str, retail: str = "") -> tuple:
        """
        Busca el producto en H4 (y H3 como fallback), abre su modal,
        gestiona personalizaciones y lo agrega al carrito.

        Retorna (added: bool, costo_total: Optional[str], costo_retail: Optional[str]).
        Los precios se extraen ANTES de confirmar el agregado, mientras el modal
        con el botón verde todavía está visible.
        """
        if not self.page:
            return False, None, None

        await self.random_delay(3.0, 4.0)

        normalized_target = self._normalize_text(product_name)
        self.logger.info(f"[didi] Buscando producto '{product_name}' en H4/H3...")

        # Buscar en H4 primero, luego H3 como fallback
        target_element = None
        for tag in ["h4", "h3", "h2"]:
            elements = await self.page.locator(tag).all()
            for el in elements:
                try:
                    if not await el.is_visible():
                        continue
                    text = await el.inner_text()
                    normalized_text = self._normalize_text(text)
                    if normalized_target == normalized_text or normalized_target in normalized_text:
                        target_element = el
                        self.logger.info(f"[didi] <{tag}> encontrado con texto: '{text}'")
                        break
                except Exception:
                    continue
            if target_element:
                break

        if not target_element:
            self.logger.warning(f"[didi] No se encontró elemento que coincida con '{product_name}'")
            return False, None, None

        # Subir al contenedor clicable
        clickable = target_element.locator(
            "xpath=ancestor::*[@role='button' or @role='link' or "
            "contains(@class, 'product') or contains(@data-testid, 'product')]"
        ).first
        if await clickable.count() == 0:
            clickable = target_element.locator("..")
        if await clickable.count() == 0:
            clickable = target_element

        try:
            await clickable.scroll_into_view_if_needed()
            await clickable.click(timeout=5000)
            await self.random_delay(1.5, 2.5)
            self.logger.info("[didi] Producto seleccionado, modal abierto")
        except Exception as e:
            self.logger.warning(f"[didi] Falló clic en contenedor del producto: {e}")
            try:
                await target_element.evaluate("el => el.click()")
                await self.random_delay(1.5, 2.5)
            except Exception:
                return False, None, None

        # Gestionar personalizaciones
        try:
            await self._handle_product_customizations(retail)
        except Exception as e:
            self.logger.debug(f"[didi] Personalización: {e}")

        # Extraer precios ANTES de confirmar el agregado
        costo_total = await self._extract_total_from_add_to_pay()
        self.logger.info(f"[didi] costo_total extraído antes de agregar: {costo_total}")

        costo_retail = await self._extract_retail_addon_price(retail)
        self.logger.info(f"[didi] costo_retail extraído: {costo_retail}")

        # Clic en Agregar
        add_button_selectors = [
            "button:has-text('Agregar e ir a pagar')",
            "button:has-text('Agregar al carrito')",
            "button:has-text('Agregar')",
            "button:has-text('Añadir')",
            "button:has-text('Añadir al carrito')",
            "button[data-testid='add-to-cart-button']",
            "button[class*='add-to-cart']",
            "button[type='submit']",
        ]

        added = False
        for selector in add_button_selectors:
            try:
                add_btn = self.page.locator(selector).first
                if await add_btn.count() > 0 and await add_btn.is_visible():
                    await add_btn.click(timeout=5000)
                    self.logger.info(f"[didi] Click en 'Agregar' usando: {selector}")
                    added = True
                    break
            except Exception:
                continue

        if not added:
            self.logger.warning(
                f"[didi] No se encontró botón de agregar para '{product_name}'. "
                "Retornando (False, None, None)."
            )
            return False, None, None


        for selector in [
            "button[aria-label='Cerrar']",
            "button[aria-label='Close']",
            "button[class*='close']",
            "[data-testid='modal-close']",
        ]:
            try:
                close_btn = self.page.locator(selector).first
                if await close_btn.count() > 0 and await close_btn.is_visible():
                    await close_btn.click(timeout=2000)
                    self.logger.info("[didi] Modal cerrado")
                    break
            except Exception:
                continue

        await self.random_delay(1.0, 2.0)
        return True, costo_total, costo_retail



    async def _handle_product_customizations(self, retail: str = "") -> None:
        """
        Recorre los grupos de personalización del modal.
        Regla:
          - Primera opción para cada grupo de radios.
          - EXCEPCIÓN en bebida: opción cuyo texto contenga `retail`.
          - Si no hay coincidencia en bebida, usa la primera opción.
        """
        if not self.page:
            return

        normalized_retail = self._normalize_text(retail) if retail else ""
        self.logger.info(f"[didi] Iniciando personalización con retail='{retail}'")

        # Detectar raíz del modal
        modal_root = None
        for selector in [
            "[role='dialog'][aria-modal='true']",
            "[role='dialog']",
            "dialog",
            "[aria-modal='true']",
            "div[class*='Modal']",
            "div[class*='modal']",
            "div[class*='popup']",
            "div[class*='Popup']",
        ]:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    modal_root = locator
                    self.logger.info(f"[didi] Modal detectado: {selector}")
                    break
            except Exception:
                continue
        if modal_root is None:
            modal_root = self.page.locator("body")
            self.logger.warning("[didi] No se detectó modal, usando body")

        # Detectar grupos de opciones
        group_lists = []
        for selector in [
            "ul.topping-list",
            "ul[class*='topping']",
            "fieldset",
            "div:has(input[type='radio'])",
        ]:
            try:
                locator = modal_root.locator(selector)
                count = await locator.count()
                if count > 0:
                    group_lists = [locator.nth(i) for i in range(count)]
                    self.logger.info(f"[didi] {count} grupos detectados con '{selector}'")
                    break
            except Exception:
                continue

        if not group_lists:
            self.logger.info("[didi] No se encontraron grupos de personalización")
            return

        async def _select_radio(radio) -> bool:
            for attempt in [
                lambda: radio.check(timeout=3000),
            ]:
                try:
                    await attempt()
                    return True
                except Exception:
                    pass
            try:
                radio_id = await radio.get_attribute("id")
                if radio_id:
                    label = modal_root.locator(f"label[for='{radio_id}']").first
                    if await label.count() > 0:
                        await label.click(timeout=3000)
                        return True
            except Exception:
                pass
            try:
                label_ancestor = radio.locator("xpath=ancestor::label[1]").first
                if await label_ancestor.count() > 0:
                    await label_ancestor.click(timeout=3000)
                    return True
            except Exception:
                pass
            try:
                await radio.click(timeout=3000)
                return True
            except Exception:
                return False

        async def _get_radio_text(radio) -> str:
            for strategy in [
                lambda: self._get_label_text_by_id(modal_root, radio),
                lambda: self._get_label_text_ancestor(radio),
                lambda: self._get_parent_text(radio),
            ]:
                try:
                    text = await strategy()
                    if text:
                        return text
                except Exception:
                    pass
            return ""

        for idx, group in enumerate(group_lists):
            try:
                if not await group.is_visible():
                    continue

                group_title = ""
                try:
                    title_loc = group.locator(
                        "xpath=preceding::h3[1] | xpath=preceding::h4[1] | xpath=preceding::strong[1]"
                    ).first
                    if await title_loc.count() > 0:
                        group_title = (await title_loc.inner_text()).strip()
                except Exception:
                    pass

                normalized_title = self._normalize_text(group_title)
                self.logger.info(f"[didi] Grupo #{idx+1} - título='{group_title}'")

                radios = group.locator("input[type='radio']:not(:disabled)")
                radio_count = await radios.count()
                if radio_count == 0:
                    continue

                is_beverage = any(
                    kw in normalized_title
                    for kw in ["bebida", "bebidas", "drink", "drinks", "refresco", "refrescos",
                               "gaseosa", "gaseosas", "elige tu bebida", "escoge tu bebida",
                               "acompaña", "acompañante"]
                )

                selected = False

                if is_beverage and normalized_retail:
                    for i in range(radio_count):
                        radio = radios.nth(i)
                        option_text = await _get_radio_text(radio)
                        if self._normalize_text(option_text) and normalized_retail in self._normalize_text(option_text):
                            if await _select_radio(radio):
                                self.logger.info(f"[didi] ✅ Bebida seleccionada por retail: '{option_text}'")
                                selected = True
                                await self.random_delay(0.3, 0.6)
                                break
                    if not selected:
                        self.logger.warning(f"[didi] Retail '{retail}' no encontrado en grupo bebida, usando primera opción")

                if not selected:
                    first_radio = radios.first
                    option_text = await _get_radio_text(first_radio)
                    if await _select_radio(first_radio):
                        self.logger.info(f"[didi] ✅ Primera opción seleccionada en '{group_title}': '{option_text}'")
                        await self.random_delay(0.3, 0.6)
                    else:
                        self.logger.warning(f"[didi] ❌ No se pudo seleccionar opción en grupo '{group_title}'")

            except Exception as e:
                self.logger.debug(f"[didi] Error en grupo #{idx+1}: {e}")

    # Helpers para _get_radio_text
    async def _get_label_text_by_id(self, modal_root, radio) -> str:
        radio_id = await radio.get_attribute("id")
        if not radio_id:
            return ""
        label = modal_root.locator(f"label[for='{radio_id}']").first
        if await label.count() > 0:
            return (await label.inner_text()).strip()
        return ""

    async def _get_label_text_ancestor(self, radio) -> str:
        label_ancestor = radio.locator("xpath=ancestor::label[1]").first
        if await label_ancestor.count() > 0:
            return (await label_ancestor.inner_text()).strip()
        return ""

    async def _get_parent_text(self, radio) -> str:
        parent = radio.locator("xpath=..")
        return (await parent.inner_text()).strip()

    # ==========================================================================
    # EXTRACCIÓN DE PRECIOS
    # ==========================================================================

    async def _extract_min_delivery_time(self) -> Optional[int]:
        """
        Busca el menor valor numérico asociado a 'min' en la página.
        Retorna el menor valor (int) o None.
        """
        if not self.page:
            return None

        try:
            all_texts = await self.page.evaluate("""() => {
                const selectors = ['p', 'span', 'div', '[class*=\"eta\"]',
                                   '[class*=\"time\"]', '[class*=\"delivery\"]'];
                const seen = new Set();
                const results = [];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        if (el.offsetParent === null) return;
                        const text = (el.innerText || '').trim();
                        if (text && text.length < 30 && /min/i.test(text) && !seen.has(text)) {
                            seen.add(text);
                            results.push(text);
                        }
                    });
                }
                return results;
            }""")
        except Exception as e:
            self.logger.warning(f"[didi] Error extrayendo tiempo de entrega: {e}")
            return None

        min_value = None
        pattern = re.compile(r'(\d{1,3})\s*[-–]?\s*\d{0,3}\s*min', re.IGNORECASE)

        for text in all_texts:
            match = pattern.search(text)
            if match:
                val = int(match.group(1))
                if min_value is None or val < min_value:
                    min_value = val
                    self.logger.info(f"[didi] Candidato tiempo envío: '{text}' → {val} min")

        if min_value is not None:
            self.logger.info(f"[didi] Tiempo de entrega mínimo: {min_value} min")
        else:
            self.logger.warning("[didi] No se encontró tiempo de entrega")

        return min_value

    async def _extract_delivery_cost_from_p(self) -> Optional[str]:

        if not self.page:
            return None

        try:
            texts = await self.page.evaluate("""() => {
                return Array.from(document.querySelectorAll('p, span, div'))
                    .filter(el => el.offsetParent !== null)
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length > 0 && t.length < 80);
            }""")
        except Exception as e:
            self.logger.warning(f"[didi] Error extrayendo costos de envío: {e}")
            return None

        delivery_keywords = ['envío', 'envio', 'domicilio', 'delivery', 'costo de envío', 'gratis']
        free_keywords = ['gratis', 'free', 'sin costo', '$0', 'envío gratis']
        price_pattern = re.compile(r'\$\s*(\d+(?:[.,]\d{1,2})?)', re.IGNORECASE)

        best_price = None
        best_price_str = None

        for text in texts:
            text_lower = text.lower()
            if not any(kw in text_lower for kw in delivery_keywords):
                continue

            if any(kw in text_lower for kw in free_keywords):
                self.logger.info(f"[didi] Envío gratis detectado: '{text}'")
                return '$0'

            match = price_pattern.search(text)
            if match:
                raw = match.group(1).replace(',', '.')
                try:
                    val = float(raw)
                    if best_price is None or val < best_price:
                        best_price = val
                        best_price_str = f'${match.group(1)}'
                except ValueError:
                    continue

        if best_price_str:
            self.logger.info(f"[didi] Costo de envío extraído: {best_price_str}")
        else:
            self.logger.warning("[didi] No se encontró costo de envío")

        return best_price_str

    async def _extract_total_from_add_to_pay(self) -> Optional[str]:

        if not self.page:
            return None

        price_regex = re.compile(r'\$\s*[\d,]+(?:\.\d{1,2})?')

        for tag in ["button", "span", "div", "a"]:
            try:
                locators = self.page.locator(tag)
                count = await locators.count()
                for i in range(count):
                    el = locators.nth(i)
                    if not await el.is_visible():
                        continue
                    text = await el.inner_text()
                    if "ir a pagar" in text.lower() or "agregar" in text.lower():
                        match = price_regex.search(text)
                        if match:
                            price = match.group(0).replace(' ', '')
                            self.logger.info(f"[didi] Total extraído de '{text.strip()}': {price}")
                            return price
            except Exception as e:
                self.logger.debug(f"[didi] Error buscando en <{tag}>: {e}")

        self.logger.warning("[didi] No se encontró botón de pago con precio")
        return None

    async def _extract_retail_addon_price(self, retail: str) -> Optional[str]:

        if not self.page:
            return None

        normalized_retail = self._normalize_text(retail) if retail else ""

        try:
            result = await self.page.evaluate(r"""(normalizedRetail) => {
                const priceRegex = /[+][\s]*[$][\s]*([\d,]+(?:[.][\d]{1,2})?)/;

                // Estrategia 1: radio chequeado que coincida con retail
                const checkedRadios = Array.from(document.querySelectorAll("input[type='radio']:checked"));
                for (const radio of checkedRadios) {
                    let container = radio.id
                        ? document.querySelector(`label[for='${radio.id}']`)
                        : null;
                    if (!container) container = radio.closest('label') || radio.parentElement;
                    if (!container) continue;
                    const containerText = (container.innerText || '').toLowerCase();
                    if (normalizedRetail && !containerText.includes(normalizedRetail)) continue;
                    const match = (container.innerText || '').match(priceRegex);
                    if (match) return '$' + match[1];
                }

                // Estrategia 2: label visible que contenga retail
                if (normalizedRetail) {
                    const candidates = Array.from(document.querySelectorAll('label, li, div, span'));
                    for (const el of candidates) {
                        if (el.offsetParent === null) continue;
                        const text = (el.innerText || '').toLowerCase();
                        if (!text.includes(normalizedRetail)) continue;
                        const match = (el.innerText || '').match(priceRegex);
                        if (match) return '$' + match[1];
                    }
                }

                // Estrategia 3: bebida genérica con precio adicional
                const beverageKws = ['coca', 'pepsi', 'sprite', 'mundet', 'agua', 'jugo', 'refresco'];
                const candidates2 = Array.from(document.querySelectorAll('label, li, div, span'));
                for (const el of candidates2) {
                    if (el.offsetParent === null) continue;
                    const text = (el.innerText || '').toLowerCase();
                    if (!beverageKws.some(k => text.includes(k))) continue;
                    const match = (el.innerText || '').match(priceRegex);
                    if (match) return '$' + match[1];
                }

                return null;
            }""", normalized_retail)

            if result:
                self.logger.info(f"[didi] Precio addon retail '{retail}': {result}")
                return result
        except Exception as e:
            self.logger.warning(f"[didi] Error extrayendo precio addon retail: {e}")

        self.logger.warning(f"[didi] No se encontró precio addon para retail '{retail}'")
        return None

    # ==========================================================================
    # GUARDAR JSON RAW
    # ==========================================================================

    def _save_raw_json(
        self,
        address_id: str,
        order: str,
        tiempo_envio: Optional[int],
        costo_envio: Optional[str],
        costo_total: Optional[str],
        costo_retail: Optional[str],
        output_dir: str = ".",
    ) -> str:
        """
        Guarda (o hace append) en raw_YYYYMMDD.json los datos extraídos.
        Misma estructura que RappiScraper.
        """
        today = datetime.now().strftime("%Y%m%d")
        filename = f"raw_{today}.json"
        filepath = os.path.join(output_dir, filename)

        record = {
            "timestamp": datetime.now().isoformat(),
            "empresa": "DIDI",
            "address_id": address_id,
            "order": order,
            "tiempo_envio": tiempo_envio,
            "costo_envio": costo_envio,
            "costo_total": costo_total,
            "costo_retail": costo_retail,
        }

        existing = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            except Exception as e:
                self.logger.warning(f"[didi] No se pudo leer {filepath}: {e}. Se sobreescribirá.")
                existing = []

        existing.append(record)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            self.logger.info(f"[didi] Datos guardados en {filepath}")
        except Exception as e:
            self.logger.error(f"[didi] Error guardando {filepath}: {e}")

        return filepath

    # ==========================================================================
    # MÉTODO PRINCIPAL
    # ==========================================================================

    async def _scrape_restaurant_search_result(
        self,
        address_data: Dict[str, Any],
        order: str,
        retail: str = "",
    ) -> Dict[str, Any]:
        """
        Orquesta el flujo completo:
          buscar restaurante → agregar producto → extraer precios → guardar JSON.
        """
        if not self.page:
            raise RuntimeError("Page no inicializada")

        address_id = address_data["id"]
        city = address_data["city"]
        zone_name = address_data["zone_name"]
        zone_type = address_data["zone_type"]

        self.logger.info(f"[didi] Buscando order: '{order}'")

        opened = await self._try_search_restaurant(order)
        screenshot_path = await self.save_screenshot(
            f"{address_id}_{self._slugify(order)}_search"
        )

        product_added = False
        costo_total = None
        costo_retail = None
        tiempo_envio = None
        costo_envio = None

        if opened:
            tiempo_envio = await self._extract_min_delivery_time()
            costo_envio = await self._extract_delivery_cost_from_p()
            self.logger.info(f"[didi] tiempo_envio: {tiempo_envio} | costo_envio: {costo_envio}")

            result = await self._add_product_by_h4(order, retail)
            product_added, costo_total, costo_retail = result
            self.logger.info(f"[didi] Producto '{order}' agregado: {product_added}")
            self.logger.info(f"[didi] costo_total: {costo_total} | costo_retail: {costo_retail}")

            await self.save_screenshot(f"{address_id}_{self._slugify(order)}_cart")
        else:
            self.logger.warning("[didi] No se pudo abrir el restaurante")

        raw_json_path = self._save_raw_json(
            address_id=address_id,
            order=order,
            tiempo_envio=tiempo_envio,
            costo_envio=costo_envio,
            costo_total=costo_total,
            costo_retail=costo_retail,
        )
        self.logger.info(f"[didi] JSON raw guardado en: {raw_json_path}")

        visible_text = await self._extract_page_text()
        available = opened or self._is_restaurant_visible(visible_text, order)
        eta_min, eta_max = self._extract_eta(visible_text)
        discount_text = self._extract_discount(visible_text)
        cart_count = await self._get_cart_count() if opened else 0

        return self.build_result(
            address_id=address_id,
            city=city,
            zone_name=zone_name,
            zone_type=zone_type,
            restaurant=order,
            product_name=order,
            product_price=None,
            delivery_fee=None,
            service_fee=None,
            eta_min=eta_min,
            eta_max=eta_max,
            discount_text=discount_text,
            total_price=None,
            available=available,
            screenshot_path=screenshot_path,
            raw_metadata={
                "order_searched": order,
                "retail_used": retail,
                "restaurant_opened": opened,
                "product_added_to_cart": product_added,
                "cart_items_count": cart_count,
                "tiempo_envio_min": tiempo_envio,
                "costo_envio": costo_envio,
                "costo_total": costo_total,
                "costo_retail": costo_retail,
                "raw_json_path": raw_json_path,
                "search_text_sample": visible_text[:1000],
            },
        )

    # ==========================================================================
    # AUXILIARES
    # ==========================================================================

    async def _extract_page_text(self) -> str:
        if not self.page:
            return ""
        try:
            return (await self.page.locator("body").inner_text()).strip()
        except Exception:
            return ""

    def _is_restaurant_visible(self, page_text: str, restaurant_name: str) -> bool:
        return self._normalize_text(restaurant_name) in self._normalize_text(page_text)

    def _extract_eta(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        patterns = [
            r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*min",
            r"(\d{1,2})\s*a\s*(\d{1,2})\s*min",
            r"(\d{1,2})\s*min",
        ]
        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if idx < 2:
                    return int(match.group(1)), int(match.group(2))
                val = int(match.group(1))
                return val, val
        return None, None

    def _extract_discount(self, text: str) -> Optional[str]:
        for pattern in [
            r"\b\d{1,3}%\s*OFF\b",
            r"\b\d+x1\b",
            r"\bEnvío gratis\b",
            r"\bDescuento\b.{0,30}",
            r"\bPromo\b.{0,30}",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return None

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        return re.sub(r"\s+", " ", text)

    def _slugify(self, text: str) -> str:
        slug = text.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        return re.sub(r"_+", "_", slug).strip("_")

    async def _get_cart_count(self) -> int:
        try:
            locator = self.page.locator(
                "[data-testid='cart-badge'], [class*='cart-count']"
            ).first
            if await locator.count() > 0:
                text = await locator.inner_text()
                match = re.search(r"\d+", text)
                if match:
                    return int(match.group())
        except Exception:
            pass
        return 0