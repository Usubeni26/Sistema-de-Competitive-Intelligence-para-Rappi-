import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from scrapers.base_scraper import BaseScraper


class RappiScraper(BaseScraper):


    def __init__(self, **kwargs):
        super().__init__(platform_name="rappi", **kwargs)
        self.base_url = "https://www.rappi.com.mx/"

    async def scrape_address(self, address_data: Dict[str, Any]) -> List[Dict[str, Any]]:

        results: List[Dict[str, Any]] = []

        address_id = address_data["id"]
        city = address_data["city"]
        zone_name = address_data["zone_name"]
        zone_type = address_data["zone_type"]

        # --- NORMALIZACIÓN: order puede ser lista o string ---
        raw_order = address_data.get("order", "")
        if isinstance(raw_order, list):
            order = raw_order[0] if raw_order else ""
            self.logger.info(f"[rappi] order era una lista, se toma el primer elemento: '{order}'")
        else:
            order = raw_order
        # ------------------------------------------------------

        # --- NORMALIZACIÓN: retail puede ser lista o string ---
        raw_retail = address_data.get("retail", "")
        if isinstance(raw_retail, list):
            retail = raw_retail[0] if raw_retail else ""
            self.logger.info(f"[rappi] retail era una lista, se toma el primer elemento: '{retail}'")
        else:
            retail = raw_retail
        # ------------------------------------------------------

        self.logger.info(f"[rappi] Iniciando scrape para {address_id} - {zone_name}")
        self.logger.info(f"[rappi] order='{order}'")
        self.logger.info(f"[rappi] retail='{retail}'")

        await self.retry(self._open_homepage)
        await self.save_screenshot(f"{address_id}_home")

        await self._try_set_address(address_data)
        await self.save_screenshot(f"{address_id}_after_address")

        if not order:
            self.logger.warning(f"[rappi] El campo 'order' está vacío para {address_id}.")
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
            self.logger.warning(f"[rappi] Error scrapeando order '{order}' en {address_id}: {e}")
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

    async def _open_homepage(self) -> None:
        await self.safe_goto(self.base_url)
        await self.page.wait_for_selector("body", timeout=10000)
        await self._dismiss_popups()
        await self.random_delay(1.0, 2.0)

    async def _dismiss_popups(self) -> None:
        possible_buttons = [
            "button:has-text('Aceptar')",
            "button:has-text('Aceptar cookies')",
            "button:has-text('Entendido')",
            "button:has-text('Continuar')",
            "button:has-text('Cerrar')",
            "button:has-text('Ahora no')",
            "button[aria-label='close']",
            "[data-testid='close-button']",
        ]
        for selector in possible_buttons:
            clicked = await self.click_if_exists(selector)
            if clicked:
                self.logger.info(f"[rappi] Popup cerrado con selector: {selector}")

    async def _diagnose_page_inputs(self) -> None:
        if not self.page:
            return
        try:
            info = await self.page.evaluate("""() => {
                const results = { buttons: [], inputs: [] };
                document.querySelectorAll('button, [role="button"], a[role="button"]').forEach(el => {
                    if (el.offsetParent !== null) {
                        results.buttons.push({
                            tag: el.tagName,
                            text: (el.innerText || '').trim().slice(0, 80),
                            testid: el.getAttribute('data-testid') || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            classes: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
                        });
                    }
                });
                document.querySelectorAll('input').forEach(el => {
                    results.inputs.push({
                        type: el.type,
                        placeholder: el.placeholder || '',
                        name: el.name || '',
                        id: el.id || '',
                        autocomplete: el.getAttribute('autocomplete') || '',
                        testid: el.getAttribute('data-testid') || '',
                        classes: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
                        visible: el.offsetParent !== null,
                    });
                });
                return results;
            }""")
            self.logger.info(f"[rappi][diagnóstico] Botones visibles: {info['buttons']}")
            self.logger.info(f"[rappi][diagnóstico] Inputs en DOM: {info['inputs']}")
        except Exception as e:
            self.logger.warning(f"[rappi][diagnóstico] Error al inspeccionar DOM: {e}")

    async def _try_set_address(self, address_data: Dict[str, Any]) -> bool:
        if not self.page:
            return False

        address = address_data["address"]
        self.logger.info(f"[rappi] Intentando setear dirección: {address}")

        # ── 1. Abrir el modal de dirección ───────────────────────────────────────
        address_trigger_selectors = [
            "[data-qa='address-container'] [role='button']",
            "[data-qa='address-container']",
            "[data-testid='address-selector']",
            "[data-testid='address-picker']",
            "[data-testid='header-address']",
            "[data-testid='change-address']",
            "button:has-text('Enviar a')",
            "button:has-text('Cambiar dirección')",
            "button:has-text('Seleccionar dirección')",
            "button:has-text('Ingresa tu dirección')",
            "button:has-text('Agregar dirección')",
            "[aria-label='Seleccionar dirección']",
            "[aria-label='Cambiar dirección']",
            "span:has-text('Enviar a')",
            "div[role='button']:has-text('dirección')",
            "header [data-testid*='address']",
        ]

        modal_opened = False
        for selector in address_trigger_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed()
                    await locator.click(timeout=4000)
                    await self.random_delay(0.8, 1.5)
                    self.logger.info(f"[rappi] Modal de dirección abierto con: {selector}")
                    modal_opened = True
                    break
            except Exception as e:
                self.logger.debug(f"[rappi] Falló trigger '{selector}': {e}")

        # ── 2. Diagnóstico + fallback JS ─────────────────────────────────────────
        if not modal_opened:
            self.logger.warning("[rappi] Ningún selector abrió el modal. Ejecutando diagnóstico...")
            await self._diagnose_page_inputs()
            try:
                clicked = await self.page.evaluate("""() => {
                    const keywords = ['dirección', 'direccion', 'enviar a', 'donde', 'ubicación', 'ubicacion'];
                    const candidates = [...document.querySelectorAll('button, [role="button"], a, div[onclick], span[onclick]')];
                    for (const el of candidates) {
                        const text = (el.innerText || el.textContent || '').toLowerCase();
                        if (keywords.some(k => text.includes(k)) && el.offsetParent !== null) {
                            el.click();
                            return el.innerText || el.tagName;
                        }
                    }
                    return null;
                }""")
                if clicked:
                    self.logger.info(f"[rappi] Modal abierto via JS fallback: '{clicked}'")
                    modal_opened = True
                    await self.random_delay(0.8, 1.5)
                else:
                    self.logger.warning("[rappi] JS fallback tampoco encontró trigger.")
            except Exception as e:
                self.logger.warning(f"[rappi] Error en JS fallback: {e}")

        # ── 3. Detectar contenedor del modal ─────────────────────────────────────
        await self.random_delay(0.5, 1.0)

        modal_container = None
        for selector in [
            "[data-testid='address-modal']",
            "[data-testid='address-dialog']",
            "[role='dialog'][aria-modal='true']",
            "[role='dialog']",
            "dialog",
            "[aria-modal='true']",
            "div[class*='Modal']",
            "div[class*='modal']",
            "div[class*='Dialog']",
            "div[class*='dialog']",
            "div[class*='Overlay']",
            "div[class*='overlay']",
            "div[class*='Address']",
            "div[class*='address']",
        ]:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=3000)
                container = self.page.locator(selector).first
                if await container.count() > 0:
                    modal_container = container
                    self.logger.info(f"[rappi] Contenedor de modal detectado: {selector}")
                    break
            except Exception:
                continue

        search_context = modal_container if modal_container else self.page.locator("body")
        if modal_container is None:
            self.logger.warning("[rappi] No se detectó contenedor de modal. Usando body.")

        # ── 4. Localizar input de dirección dentro del modal ─────────────────────
        input_element = None
        for selector in [
            "input[data-testid*='address']",
            "input[data-testid*='location']",
            "input[placeholder*='dirección']",
            "input[placeholder*='Dirección']",
            "input[placeholder*='Buscar dirección']",
            "input[placeholder*='¿A dónde']",
            "input[placeholder*='Ingresa']",
            "input[autocomplete='street-address']",
            "input[autocomplete*='address']",
            "input[name*='address']",
            "input[id*='address']",
            "input[name*='location']",
            "input[id*='location']",
            "input[type='text']",
        ]:
            try:
                candidate = search_context.locator(selector).first
                if await candidate.count() > 0:
                    input_element = candidate
                    self.logger.info(f"[rappi] Input de dirección encontrado: {selector}")
                    break
            except Exception:
                continue

        if not input_element:
            self.logger.error("[rappi] No se encontró input de dirección.")
            await self._diagnose_page_inputs()
            return False

        # ── 5. Escribir la dirección ─────────────────────────────────────────────
        try:
            await input_element.scroll_into_view_if_needed()
            await input_element.click()
            await input_element.fill("")
            await input_element.type(address, delay=60)
            self.logger.info(f"[rappi] Dirección escrita: '{address}'")
        except Exception as e:
            self.logger.error(f"[rappi] Error al escribir dirección: {e}")
            return False

        await self.random_delay(1.0, 2.0)

        # ── 6. Seleccionar la primera sugerencia ─────────────────────────────────
        selected = False
        for selector in [
            "[data-qa='suggestion-item'] button",
            "[data-qa='suggestion-item']",
            "[data-testid='address-suggestion']",
            "[data-testid*='suggestion']",
            "[role='listbox'] [role='option']",
            "[role='option']",
            "li[role='option']",
            "ul li button",
            "ul li",
        ]:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=4000)
                suggestions = await self.page.query_selector_all(selector)
                if suggestions:
                    await suggestions[0].click()
                    self.logger.info(f"[rappi] Primera sugerencia seleccionada ({selector})")
                    selected = True
                    break
            except Exception:
                continue

        if not selected:
            self.logger.warning("[rappi] No se encontraron sugerencias. Usando Enter.")
            try:
                await input_element.press("Enter")
            except Exception:
                pass

        await self.random_delay(0.5, 1.0)

        # ── 7. Confirmar dirección ───────────────────────────────────────────────
        confirm_clicked = False
        for selector in [
            "[data-qa='confirm-address']",
            "button:has-text('Confirmar dirección')",
            "button:has-text('Confirmar')",
            "button:has-text('Usar esta dirección')",
            "button:has-text('Guardar')",
            "button:has-text('Continuar')",
            "button[type='submit']",
        ]:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=5000)
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed()
                    await locator.click(timeout=3000)
                    self.logger.info(f"[rappi] Confirmación de dirección clicada: {selector}")
                    confirm_clicked = True
                    break
            except Exception:
                continue

        if not confirm_clicked:
            self.logger.warning("[rappi] No se encontró botón de confirmación.")

        # ── 8. Guardar dirección (popup adicional) ───────────────────────────────
        for selector in [
            "button:has-text('Guardar dirección')",
            "button:has-text('Guardar')",
        ]:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=5000)
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    await locator.click(timeout=3000)
                    self.logger.info(f"[rappi] 'Guardar dirección' clicado: {selector}")
                    break
            except Exception:
                continue

        # ── 9. Esperar cierre del modal ──────────────────────────────────────────
        try:
            await self.page.wait_for_selector("[role='dialog']", state="hidden", timeout=6000)
            self.logger.info("[rappi] Modal de dirección cerrado.")
        except Exception:
            self.logger.warning("[rappi] No se confirmó cierre del modal. Continuando.")

        await self.random_delay(2.0, 3.0)
        self.logger.info("[rappi] Seteo de dirección finalizado.")
        return True

    async def _try_search_restaurant(self, query: str) -> bool:

        if not self.page:
            return False

        query = str(query).strip()
        if not query:
            return False

        global_search_input_selectors = [
            "[data-qa='search-wrapper-desktop'] [data-qa='input']",
            "[data-qa='search-wrapper-desktop'] input",
            "[data-qa='input']",
            "input[placeholder*='Buscar']",
            "input[placeholder*='Restaurantes']",
            "input[type='search']",
        ]

        input_locator = None

        # 1. Encontrar input y escribir búsqueda
        for selector in global_search_input_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed()
                    await locator.click()
                    await locator.fill("")
                    await locator.type(query, delay=50)
                    await self.random_delay(1.2, 2.0)
                    input_locator = locator
                    self.logger.info(f"[rappi] Búsqueda escrita: '{query}' con selector: {selector}")
                    break
            except Exception as e:
                self.logger.debug(f"[rappi] Falló selector de búsqueda '{selector}': {e}")

        if not input_locator:
            search_url = f"{self.base_url}search?query={query.replace(' ', '%20')}"
            self.logger.warning(f"[rappi] No se encontró buscador global. Fallback URL: {search_url}")
            await self.safe_goto(search_url)
            await self.random_delay(2.0, 3.0)
        else:
            await self.random_delay(1.5, 2.5)

        # 2. Intentar seleccionar resultado exacto o parcial
        selected = await self._select_search_result(query)

        if selected:
            self.logger.info(f"[rappi] Resultado seleccionado correctamente para '{query}'")
            await self.random_delay(2.0, 3.0)
            return True

        # 3. Fallback: Enter para ejecutar búsqueda
        self.logger.warning(f"[rappi] No se encontró coincidencia visible para '{query}'. Fallback con Enter.")
        try:
            if input_locator:
                await input_locator.press("Enter")
                await self.random_delay(2.0, 3.0)

                selected = await self._select_search_result(query)
                if selected:
                    self.logger.info(f"[rappi] Resultado seleccionado después de Enter para '{query}'")
                    await self.random_delay(2.0, 3.0)
                    return True
        except Exception as e:
            self.logger.warning(f"[rappi] Error haciendo Enter en búsqueda: {e}")

        return False

    async def _select_search_result(self, query: str) -> bool:

        if not self.page:
            return False

        normalized_query = self._normalize_text(query)

        # Intento 0: usar get_by_text con coincidencia exacta visible
        try:
            exact_text_locator = self.page.get_by_text(query, exact=True).first
            if await exact_text_locator.count() > 0 and await exact_text_locator.is_visible():
                self.logger.info(f"[rappi] get_by_text exact encontró '{query}'")
                try:
                    await exact_text_locator.click(timeout=4000)
                    return True
                except Exception:
                    clickable = exact_text_locator.locator(
                        "xpath=ancestor::a[1] | xpath=ancestor::*[@role='link'][1] | xpath=ancestor::button[1]"
                    ).first
                    if await clickable.count() > 0:
                        await clickable.click(timeout=4000)
                        return True
        except Exception as e:
            self.logger.debug(f"[rappi] get_by_text exact falló: {e}")

        result_selectors = [
            "a[href*='/tiendas/']",
            "a[href*='/store/']",
            "a[href*='/restaurantes/']",
            "a[href*='/restaurant/']",
            "[data-testid*='store'] a",
            "[data-testid*='restaurant'] a",
            "[data-testid*='store']",
            "[data-testid*='restaurant']",
            "[data-qa*='store']",
            "[data-qa*='restaurant']",
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

                self.logger.info(f"[rappi] Evaluando {count} candidatos con selector: {selector}")

                for i in range(count):
                    try:
                        el = locator.nth(i)

                        if not await el.is_visible():
                            continue

                        text = (await el.inner_text()).strip()
                        if not text:
                            continue

                        normalized_text = self._normalize_text(text)

                        if normalized_text == normalized_query:
                            exact_match = el
                            self.logger.info(f"[rappi] Coincidencia EXACTA encontrada: '{text}'")
                            break

                        if normalized_query in normalized_text:
                            if partial_match is None:
                                partial_match = el
                                self.logger.info(f"[rappi] Coincidencia PARCIAL candidata: '{text}'")

                    except Exception:
                        continue

                if exact_match:
                    break

            except Exception as e:
                self.logger.debug(f"[rappi] Error revisando selector '{selector}': {e}")

        target = exact_match or partial_match

        if target:
            try:
                await target.scroll_into_view_if_needed()
                await target.click(timeout=5000)
                self.logger.info("[rappi] Click realizado en el resultado seleccionado.")
                return True
            except Exception as e:
                self.logger.warning(f"[rappi] Error al hacer click en el resultado: {e}")
                try:
                    await target.evaluate("(el) => el.click()")
                    self.logger.info("[rappi] Click JS realizado en el resultado seleccionado.")
                    return True
                except Exception as e2:
                    self.logger.warning(f"[rappi] También falló JS click: {e2}")

        self.logger.warning(f"[rappi] No se encontró resultado coincidente para '{query}'")
        return False

    # --------------------------------------------------------------------------
    # MÉTODO: Agregar producto buscando en H4 para tiempo domicilio y valor domicilio
    # --------------------------------------------------------------------------

    async def _add_product_by_h4(self, product_name: str, retail: str = "") -> tuple:

        if not self.page:
            return False

        await self.random_delay(3.0, 4.0)

        normalized_target = self._normalize_text(product_name)
        self.logger.info(f"[rappi] Buscando producto '{product_name}' en H4...")

        h4_elements = await self.page.locator("h4").all()
        if not h4_elements:
            self.logger.warning("[rappi] No se encontraron elementos H4 en la página")
            return False

        target_h4 = None
        for h4 in h4_elements:
            try:
                if not await h4.is_visible():
                    continue
                text = await h4.inner_text()
                normalized_text = self._normalize_text(text)
                if normalized_target == normalized_text or normalized_target in normalized_text:
                    target_h4 = h4
                    self.logger.info(f"[rappi] H4 encontrado con texto: '{text}'")
                    break
            except Exception:
                continue

        if not target_h4:
            self.logger.warning(f"[rappi] No se encontró H4 que coincida con '{product_name}'")
            return False

        clickable = target_h4.locator(
            "xpath=ancestor::*[@role='button' or @role='link' or contains(@class, 'product') or contains(@data-testid, 'product')]"
        ).first
        if await clickable.count() == 0:
            clickable = target_h4.locator("..")
        if await clickable.count() == 0:
            clickable = target_h4

        try:
            await clickable.scroll_into_view_if_needed()
            await clickable.click(timeout=5000)
            await self.random_delay(1.5, 2.5)
            self.logger.info("[rappi] Producto seleccionado, modal abierto")
        except Exception as e:
            self.logger.warning(f"[rappi] Falló clic en contenedor del producto: {e}")
            try:
                await target_h4.evaluate("el => el.click()")
                await self.random_delay(1.5, 2.5)
            except Exception:
                return False

        try:
            await self._handle_product_customizations(retail)
        except Exception as e:
            self.logger.debug(f"[rappi] Personalización de producto: {e}")


        costo_total = await self._extract_total_from_add_to_pay()
        self.logger.info(f"[rappi] costo_total extraído antes de agregar: {costo_total}")


        costo_retail = await self._extract_retail_addon_price(retail)
        self.logger.info(f"[rappi] costo_retail extraído: {costo_retail}")

        add_button_selectors = [
            "button:has-text('Agregar e ir a pagar')",
            "button:has-text('Agregar')",
            "button:has-text('Añadir')",
            "button:has-text('Agregar al carrito')",
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
                    self.logger.info(f"[rappi] Click en 'Agregar' usando selector: {selector}")
                    added = True
                    break
            except Exception:
                continue

        if not added:
            self.logger.warning(
                f"[rappi] No se encontró botón de agregar para '{product_name}'. "
                "Retornando (False, None, None)."
            )
            return False, None, None

        close_selectors = [
            "button[aria-label='Cerrar']",
            "button[class*='close']",
            "[data-testid='modal-close']",
        ]
        for selector in close_selectors:
            try:
                close_btn = self.page.locator(selector).first
                if await close_btn.count() > 0 and await close_btn.is_visible():
                    await close_btn.click(timeout=2000)
                    self.logger.info("[rappi] Modal cerrado")
                    break
            except Exception:
                continue

        await self.random_delay(1.0, 2.0)
        return True, costo_total, costo_retail

    # --------------------------------------------------------------------------
    # MÉTODO: Manejo de personalizaciones
    # --------------------------------------------------------------------------

    async def _handle_product_customizations(self, retail: str = "") -> None:

        if not self.page:
            return

        normalized_retail = self._normalize_text(retail) if retail else ""
        self.logger.info(f"[rappi] Iniciando personalización de producto con retail='{retail}'")

        modal_root = None
        for selector in [
            "[role='dialog'][aria-modal='true']",
            "[role='dialog']",
            "dialog",
            "[aria-modal='true']",
            "div[class*='Modal']",
            "div[class*='modal']",
        ]:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    modal_root = locator
                    self.logger.info(f"[rappi] Modal detectado: {selector}")
                    break
            except Exception:
                continue
        if modal_root is None:
            modal_root = self.page.locator("body")
            self.logger.warning("[rappi] No se detectó modal, usando body")

        group_lists = []
        for selector in [
            "ul.topping-list",
            "ul[class*='topping-list']",
            "fieldset",
            "div:has(input[type='radio'])",
        ]:
            try:
                locator = modal_root.locator(selector)
                count = await locator.count()
                if count > 0:
                    group_lists = [locator.nth(i) for i in range(count)]
                    self.logger.info(f"[rappi] Detectados {count} grupos con '{selector}'")
                    break
            except Exception:
                continue

        if not group_lists:
            self.logger.info("[rappi] No se encontraron grupos de personalización")
            return

        async def _select_radio(radio) -> bool:
            try:
                if await radio.is_visible() and await radio.is_enabled():
                    await radio.check(timeout=3000)
                    return True
            except Exception:
                pass
            try:
                radio_id = await radio.get_attribute("id")
                if radio_id:
                    label = modal_root.locator(f"label[for='{radio_id}']").first
                    if await label.count() > 0 and await label.is_visible():
                        await label.click(timeout=3000)
                        return True
            except Exception:
                pass
            try:
                label_ancestor = radio.locator("xpath=ancestor::label[1]").first
                if await label_ancestor.count() > 0 and await label_ancestor.is_visible():
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
            try:
                radio_id = await radio.get_attribute("id")
                if radio_id:
                    label = modal_root.locator(f"label[for='{radio_id}']").first
                    if await label.count() > 0:
                        text = (await label.inner_text()).strip()
                        if text:
                            return text
            except Exception:
                pass
            try:
                label_ancestor = radio.locator("xpath=ancestor::label[1]").first
                if await label_ancestor.count() > 0:
                    text = (await label_ancestor.inner_text()).strip()
                    if text:
                        return text
            except Exception:
                pass
            try:
                parent = radio.locator("xpath=..")
                text = (await parent.inner_text()).strip()
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
                    title_locator = group.locator("xpath=preceding::h3[1] | xpath=preceding::h4[1] | xpath=preceding::strong[1]").first
                    if await title_locator.count() > 0:
                        group_title = (await title_locator.inner_text()).strip()
                except Exception:
                    pass
                normalized_title = self._normalize_text(group_title)

                self.logger.info(f"[rappi] Procesando grupo #{idx+1} - título='{group_title}'")

                radios = group.locator("input[type='radio']:not(:disabled)")
                radio_count = await radios.count()
                if radio_count == 0:
                    self.logger.debug(f"[rappi] Grupo #{idx+1} sin radios")
                    continue

                is_beverage = any(
                    kw in normalized_title
                    for kw in ["bebida", "bebidas", "drink", "drinks", "refresco", "refrescos",
                               "gaseosa", "gaseosas", "elige tu bebida", "escoge tu bebida", "acompaña tu combo"]
                )

                selected = False

                if is_beverage and normalized_retail:
                    self.logger.info(f"[rappi] Grupo de bebida - buscando coincidencia con retail='{retail}'")
                    for i in range(radio_count):
                        radio = radios.nth(i)
                        option_text = await _get_radio_text(radio)
                        normalized_option = self._normalize_text(option_text)
                        self.logger.debug(f"[rappi] Opción {i+1}: '{option_text}'")
                        if normalized_retail in normalized_option:
                            if await _select_radio(radio):
                                self.logger.info(f"[rappi] ✅ Seleccionada opción de bebida por retail: '{option_text}'")
                                selected = True
                                await self.random_delay(0.3, 0.6)
                                break
                    if not selected:
                        self.logger.warning(f"[rappi] No se encontró opción que contenga '{retail}' en grupo de bebida, se usará primera opción")

                if not selected:
                    first_radio = radios.first
                    option_text = await _get_radio_text(first_radio)
                    if await _select_radio(first_radio):
                        self.logger.info(f"[rappi] ✅ Seleccionada primera opción del grupo '{group_title}': '{option_text}'")
                        await self.random_delay(0.3, 0.6)
                    else:
                        self.logger.warning(f"[rappi] ❌ No se pudo seleccionar ninguna opción en grupo '{group_title}'")

            except Exception as e:
                self.logger.debug(f"[rappi] Error en grupo #{idx+1}: {e}")

        try:
            selects = await modal_root.locator("select:not(:disabled)").all()
            for select in selects:
                try:
                    options = await select.locator("option:not(:disabled)").all()
                    if len(options) > 1:
                        await options[1].click()
                        self.logger.info("[rappi] Opción de select cambiada")
                        await self.random_delay(0.3, 0.6)
                except Exception:
                    continue
        except Exception:
            pass

    async def _get_cart_count(self) -> int:
        """Retorna el número de items en el carrito (si es visible)"""
        try:
            cart_selector = "[data-testid='cart-badge'], [class*='cart-count'], span:has-text('Carrito') + span"
            locator = self.page.locator(cart_selector).first
            if await locator.count() > 0:
                text = await locator.inner_text()
                match = re.search(r"\d+", text)
                if match:
                    return int(match.group())
        except Exception:
            pass
        return 0

    # --------------------------------------------------------------------------
    # MÉTODOS: Extracción de datos de precio
    # --------------------------------------------------------------------------

    async def _extract_min_delivery_time(self) -> Optional[int]:

        if not self.page:
            return None

        try:
            all_texts = await self.page.evaluate("""() => {
                const selectors = [
                    'p', 'span', 'div',
                    '[data-testid="typography"]',
                    '[class*="eta"]', '[class*="time"]',
                    '[class*="delivery"]', '[class*="entrega"]',
                ];
                const seen = new Set();
                const results = [];
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        if (el.offsetParent === null) return;
                        const text = (el.innerText || '').trim();
                        // Solo textos cortos que contengan "min" para evitar ruido
                        if (text && text.length < 30 && /min/i.test(text) && !seen.has(text)) {
                            seen.add(text);
                            results.push(text);
                        }
                    });
                }
                return results;
            }""")
        except Exception as e:
            self.logger.warning(f"[rappi] Error extrayendo tiempo de entrega: {e}")
            return None

        min_value = None
        pattern = re.compile(r'(\d{1,3})\s*[-–]?\s*\d{0,3}\s*min', re.IGNORECASE)

        for text in all_texts:
            match = pattern.search(text)
            if match:
                val = int(match.group(1))
                if min_value is None or val < min_value:
                    min_value = val
                    self.logger.info(f"[rappi] Candidato tiempo envío: '{text}' → {val} min")

        if min_value is not None:
            self.logger.info(f"[rappi] Tiempo de entrega mínimo extraído: {min_value} min")
        else:
            self.logger.warning("[rappi] No se encontró tiempo de entrega en la página")

        return min_value

    async def _extract_delivery_cost_from_p(self) -> Optional[str]:
 
        if not self.page:
            return None

        try:
            p_texts = await self.page.evaluate("""() => {
                return Array.from(document.querySelectorAll('p'))
                    .filter(el => el.offsetParent !== null)
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length > 0);
            }""")
        except Exception as e:
            self.logger.warning(f"[rappi] Error extrayendo costos de envío de <p>: {e}")
            return None

        delivery_keywords = ['envío', 'envios', 'envio', 'domicilio', 'delivery', 'costo de envío', 'gratis']
        free_keywords = ['gratis', 'free', 'sin costo', '$0']
        price_pattern = re.compile(r'\$\s*(\d+(?:[.,]\d{1,2})?)', re.IGNORECASE)

        best_price = None
        best_price_str = None

        for text in p_texts:
            text_lower = text.lower()
            has_delivery_kw = any(kw in text_lower for kw in delivery_keywords)
            if not has_delivery_kw:
                continue

            # Caso: envío gratis
            if any(kw in text_lower for kw in free_keywords):
                self.logger.info(f"[rappi] Envío gratis detectado en <p>: '{text}'")
                return '$0'

            # Caso: tiene precio
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
            self.logger.info(f"[rappi] Costo de envío extraído de <p>: {best_price_str}")
        else:
            self.logger.warning("[rappi] No se encontró costo de envío en etiquetas <p>")

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
                    if "ir a pagar" in text.lower():
                        match = price_regex.search(text)
                        if match:
                            price = match.group(0).replace(' ', '')
                            self.logger.info(f"[rappi] Total extraído de '{text.strip()}': {price}")
                            return price
            except Exception as e:
                self.logger.debug(f"[rappi] Error buscando en <{tag}>: {e}")

        self.logger.warning("[rappi] No se encontró el texto 'ir a pagar' con precio")
        return None

    async def _extract_retail_addon_price(self, retail: str) -> Optional[str]:

        if not self.page:
            return None

        normalized_retail = self._normalize_text(retail) if retail else ""

        try:
            result = await self.page.evaluate(r"""(normalizedRetail) => {
                const priceRegex = /[+][\s]*[$][\s]*([\d,]+(?:[.][\d]{1,2})?)/;

               
                const checkedRadios = Array.from(
                    document.querySelectorAll("input[type='radio']:checked")
                );
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

                
                // y un precio adicional con formato '+ $XX'
                if (normalizedRetail) {
                    const candidates = Array.from(
                        document.querySelectorAll('label, li, div, span')
                    );
                    for (const el of candidates) {
                        if (el.offsetParent === null) continue;
                        const text = (el.innerText || '').toLowerCase();
                        if (!text.includes(normalizedRetail)) continue;
                        const match = (el.innerText || '').match(priceRegex);
                        if (match) return '$' + match[1];
                    }
                }

                
                const beverageKws = ['coca'];
                const candidates2 = Array.from(
                    document.querySelectorAll('label, li, div, span')
                );
                for (const el of candidates2) {
                    if (el.offsetParent === null) continue;
                    const text = (el.innerText || '').toLowerCase();
                    const hasBevKw = beverageKws.some(k => text.includes(k));
                    if (!hasBevKw) continue;
                    const match = (el.innerText || '').match(priceRegex);
                    if (match) return '$' + match[1];
                }

                return null;
            }""", normalized_retail)

            if result:
                self.logger.info(f"[rappi] Precio addon retail '{retail}': {result}")
                return result
        except Exception as e:
            self.logger.warning(f"[rappi] Error extrayendo precio addon retail: {e}")

        self.logger.warning(f"[rappi] No se encontró precio addon para retail '{retail}'")
        return None

    async def _extract_retail_price(self, retail: str) -> Optional[str]:

        if not self.page:
            return None

        normalized_retail = self._normalize_text(retail) if retail else ""

        try:
            result = await self.page.evaluate(r"""(normalizedRetail) => {
                const priceRegex = /\$\s*[\d,]+(?:\.\d{1,2})?/;

               
                const checkedRadios = Array.from(
                    document.querySelectorAll("input[type='radio']:checked")
                );
                for (const radio of checkedRadios) {
                    let container = radio.id
                        ? document.querySelector(`label[for='${radio.id}']`)
                        : null;
                    if (!container) container = radio.closest('label') || radio.parentElement;
                    if (!container) continue;

                    const containerText = (container.innerText || '').toLowerCase();
                    const matchesRetail = normalizedRetail
                        ? containerText.includes(normalizedRetail)
                        : false;
                    const isBeverage = ['bebida', 'refresco', 'gaseosa', 'drink', 'agua', 'jugo'].some(
                        kw => containerText.includes(kw)
                    );

                    if (matchesRetail || isBeverage) {
                        
                       
                        const priceEl = container.querySelector('[data-testid="typography"]');
                        if (priceEl) {
                            const text = (priceEl.innerText || '').trim();
                            const match = text.match(priceRegex);
                            if (match) return match[0].replace(/\s/g, '');
                        }
                        
                        const fullText = (container.innerText || '').trim();
                        const match = fullText.match(priceRegex);
                        if (match) return match[0].replace(/\s/g, '');
                    }
                }

                // Estrategia 2: buscar directamente por texto de retail en labels
                if (normalizedRetail) {
                    const allLabels = Array.from(document.querySelectorAll('label, li, div'));
                    for (const el of allLabels) {
                        const text = (el.innerText || '').toLowerCase();
                        if (!text.includes(normalizedRetail)) continue;
                        const priceEl = el.querySelector('[data-testid="typography"]');
                        if (priceEl) {
                            const priceText = (priceEl.innerText || '').trim();
                            const match = priceText.match(priceRegex);
                            if (match) return match[0].replace(/\s/g, '');
                        }
                        const match = text.match(priceRegex);
                        if (match) return match[0].replace(/\s/g, '');
                    }
                }

                return null;
            }""", normalized_retail)

            if result:
                self.logger.info(f"[rappi] Precio de retail '{retail}' extraído: {result}")
                return result
        except Exception as e:
            self.logger.warning(f"[rappi] Error extrayendo precio de retail: {e}")

        # Fallback Playwright: selector directo por data-testid solamente (sin clase dinámica)
        try:
            locator = self.page.locator('[data-testid="typography"]').first
            if await locator.count() > 0 and await locator.is_visible():
                text = await locator.inner_text()
                match = re.search(r'\$\s*[\d,]+(?:\.\d{1,2})?', text)
                if match:
                    price = match.group(0).replace(' ', '')
                    self.logger.info(f"[rappi] Precio retail extraído por data-testid typography: {price}")
                    return price
        except Exception:
            pass

        self.logger.warning(f"[rappi] No se encontró precio para retail '{retail}'")
        return None

    async def _open_cart_panel(self) -> bool:

        if not self.page:
            return False

        # Selector estable del botón de pago (sin clases CSS dinámicas)
        pay_button_selectors = [
            "button[data-qa='primary-button']",
        ]

        for selector in pay_button_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    self.logger.info(f"[rappi] Botón de pago ya visible: {selector}")
                    return True
            except Exception:
                continue

        # Si no está visible, intentar abrir el carrito
        self.logger.info("[rappi] Botón de pago no visible, intentando abrir carrito...")
        cart_trigger_selectors = [
            "[data-testid='cart-button']",
            "[data-testid='shopping-cart']",
            "[data-testid='cart']",
            "[aria-label='Ver carrito']",
            "[aria-label='Carrito']",
            "button:has-text('Ver carrito')",
            "button:has-text('Carrito')",
            "[class*='cart-button']",
            "[class*='CartButton']",
        ]

        for selector in cart_trigger_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=3000)
                    await self.random_delay(1.0, 1.5)
                    self.logger.info(f"[rappi] Carrito abierto con: {selector}")
                    return True
            except Exception:
                continue

        self.logger.warning("[rappi] No se pudo confirmar visibilidad del panel del carrito")
        return False

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

        today = datetime.now().strftime("%Y%m%d")
        filename = f"raw_{today}.json"
        filepath = os.path.join(output_dir, filename)

        record = {
            "timestamp": datetime.now().isoformat(),
            "empresa": "RAPPI",
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
                self.logger.warning(f"[rappi] No se pudo leer {filepath}: {e}. Se sobreescribirá.")
                existing = []

        existing.append(record)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            self.logger.info(f"[rappi] Datos guardados en {filepath}")
        except Exception as e:
            self.logger.error(f"[rappi] Error guardando {filepath}: {e}")

        return filepath

    # --------------------------------------------------------------------------
    # MÉTODO PRINCIPAL: busca restaurante, agrega producto y extrae precios
    # --------------------------------------------------------------------------

    async def _scrape_restaurant_search_result(
        self,
        address_data: Dict[str, Any],
        order: str,
        retail: str = "",
    ) -> Dict[str, Any]:
      
        if not self.page:
            raise RuntimeError("Page no inicializada")

        address_id = address_data["id"]
        city = address_data["city"]
        zone_name = address_data["zone_name"]
        zone_type = address_data["zone_type"]

        self.logger.info(f"[rappi] Buscando order: '{order}'")

        opened = await self._try_search_restaurant(order)
        screenshot_path = await self.save_screenshot(
            f"{address_id}_{self._slugify(order)}_search"
        )

        product_added = False
        costo_total = None
        costo_retail = None

        if opened:
            # FIX: extraer tiempo_envio y costo_envio AQUÍ, mientras la página del
            # restaurante está visible y antes de abrir el modal del producto.
            tiempo_envio = await self._extract_min_delivery_time()
            costo_envio = await self._extract_delivery_cost_from_p()
            self.logger.info(f"[rappi] tiempo_envio: {tiempo_envio} | costo_envio: {costo_envio}")

            # _add_product_by_h4 retorna (added, costo_total, costo_retail)
            # extrayendo precios ANTES de hacer click en Agregar, mientras el modal está abierto.
            result = await self._add_product_by_h4(order, retail)
            product_added, costo_total, costo_retail = result
            self.logger.info(f"[rappi] Producto '{order}' agregado al carrito: {product_added}")
            self.logger.info(f"[rappi] costo_total: {costo_total} | costo_retail: {costo_retail}")

            await self.save_screenshot(f"{address_id}_{self._slugify(order)}_cart")
        else:
            self.logger.warning("[rappi] No se pudo abrir el restaurante, no se agrega producto")
            tiempo_envio = None
            costo_envio = None

        # ── Guardar en raw_YYYYMMDD.json ──────────────────────────────────────
        raw_json_path = self._save_raw_json(
            address_id=address_id,
            order=order,
            tiempo_envio=tiempo_envio,
            costo_envio=costo_envio,
            costo_total=costo_total,
            costo_retail=costo_retail,
        )
        self.logger.info(f"[rappi] JSON raw guardado en: {raw_json_path}")

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

    # --------------------------------------------------------------------------
    # MÉTODOS AUXILIARES
    # --------------------------------------------------------------------------

    async def _extract_page_text(self) -> str:
        if not self.page:
            return ""
        try:
            body = await self.page.locator("body").inner_text()
            return body.strip()
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
                else:
                    value = int(match.group(1))
                    return value, value
        return None, None

    def _extract_discount(self, text: str) -> Optional[str]:
        promo_patterns = [
            r"\b\d{1,3}%\s*OFF\b",
            r"\b\d+x1\b",
            r"\bEnvío gratis\b",
            r"\bEnvio gratis\b",
            r"\bDescuento\b.{0,30}",
            r"\bPromo\b.{0,30}",
        ]
        for pattern in promo_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return None

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def _slugify(self, text: str) -> str:
        slug = text.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug