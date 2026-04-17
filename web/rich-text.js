/**
 * rich-text.js — Parser + renderer de texto libre para detalle de ofertas.
 *
 * Uso:
 *   const html = window.richText.format(rawText, { truncate: true });
 *   // o, compatibilidad con el helper antiguo:
 *   const html = window.formatRichText(rawText);
 *
 * Tubería:
 *   normalize → dedupe → explodeInlineListAfterHeader
 *   → explodeInlineEnumerations → liftInlineHeaders
 *   → splitIntoStructuredBlocks → renderStructuredContent
 *
 * No muta el texto fuente: sólo reestructura visualmente.
 */
(function () {
  'use strict';

  // ─────────────────────────────────────────────────────────────
  // 1. Encabezados conocidos del sector público chileno
  // ─────────────────────────────────────────────────────────────
  const KNOWN_HEADERS = [
    'requisitos principales', 'requisitos del cargo', 'requisitos excluyentes',
    'requisitos deseables', 'requisitos generales', 'requisitos específicos',
    'requisitos especificos', 'requisitos exigibles', 'requisitos legales',
    'requisitos obligatorios', 'requisitos mínimos', 'requisitos minimos',
    'funciones del cargo', 'funciones principales', 'funciones específicas',
    'funciones especificas', 'funciones', 'principales funciones',
    'descripción del cargo', 'descripcion del cargo',
    'descripción de funciones', 'descripcion de funciones',
    'descripción', 'descripcion',
    'competencias requeridas', 'competencias específicas', 'competencias especificas',
    'competencias conductuales', 'competencias técnicas', 'competencias tecnicas',
    'competencias', 'habilidades interpersonales', 'habilidades blandas',
    'habilidades técnicas', 'habilidades tecnicas', 'habilidades',
    'especialización y capacitación', 'especializacion y capacitacion',
    'especialización', 'especializacion',
    'capacitación', 'capacitacion',
    'capacitaciones deseables',
    'formación educacional', 'formacion educacional',
    'formación académica', 'formacion academica',
    'formación', 'formacion',
    'conocimientos claves', 'conocimientos específicos', 'conocimientos especificos',
    'conocimientos', 'experiencia laboral', 'experiencia profesional',
    'experiencia deseable', 'experiencia específica', 'experiencia especifica',
    'experiencia', 'probidad y conducta ética', 'probidad y conducta etica',
    'probidad', 'perfil del cargo', 'perfil profesional', 'perfil',
    'objetivo del cargo', 'objetivo', 'misión del cargo', 'mision del cargo',
    'misión', 'mision',
    'documentos requeridos', 'documentos a presentar',
    'documentación requerida', 'documentacion requerida',
    'antecedentes requeridos', 'antecedentes',
    'beneficios', 'condiciones del contrato', 'condiciones',
    'renta', 'remuneración', 'remuneracion',
    'jornada laboral', 'jornada',
    'lugar de trabajo', 'ubicación', 'ubicacion',
    'vacantes', 'etapas del proceso', 'proceso de selección',
    'proceso de seleccion', 'dependencia', 'supervisa a'
  ];

  const HEADER_TOKENS = new Set(KNOWN_HEADERS.map(function (h) { return h.toLowerCase(); }));

  const EXCLUYENTE_NEEDLES = [
    'excluyente', 'excluyentes', 'obligatori', 'legales', 'mínimo', 'minimo'
  ];
  const DESEABLE_NEEDLES = [
    'deseable', 'deseables', 'opcional'
  ];

  // Palabras que sugieren que un ítem separado por coma es en realidad
  // prosa/redacción (verbos conjugados, conectores) → no se divide.
  const PROSE_TOKENS = /\b(es|son|debe|deberá|debera|tendrá|tendra|tiene|requiere|corresponde|corresponderá|correspondera|realizar|realizará|realizara|evaluar|evaluará|evaluara|coordinar|coordinará|coordinara|gestionar|supervisar|elaborar|elaborará|elaborara|apoyar|colaborar|desarrollar|mantener|velar|asegurar|implementar|participar|liderar|además|ademas|sin embargo|por lo tanto|de acuerdo|así como|asi como|entre otros|entre otras)\b/i;

  // ─────────────────────────────────────────────────────────────
  // 2. Normalización
  // ─────────────────────────────────────────────────────────────
  function normalizeText(raw) {
    if (!raw) return '';
    var t = String(raw);
    t = t.replace(/\r\n?/g, '\n');
    t = t.replace(/[\u200B-\u200D\uFEFF]/g, '');
    t = t.replace(/\u00A0/g, ' ');
    // Colapsa espacios y tabs sin tocar saltos de línea
    t = t.replace(/[ \t]+/g, ' ');
    // Trim por línea
    t = t.split('\n').map(function (l) { return l.trim(); }).join('\n');
    // Más de 2 saltos seguidos → 2
    t = t.replace(/\n{3,}/g, '\n\n');
    // Puntuación repetida artificialmente (...,,,  ..  ;; )
    t = t.replace(/([,;:!?]){2,}/g, '$1');
    t = t.replace(/\.{4,}/g, '...');
    // Espacio antes de puntuación
    t = t.replace(/ +([,.;:!?])/g, '$1');
    // Segunda pasada por si el colapso de espacios juntó dos signos distintos
    t = t.replace(/([,;:!?]){2,}/g, '$1');
    // Secuencias largas de guiones/underscores usadas como separadores
    t = t.replace(/[-_]{4,}/g, '');
    return t.trim();
  }

  function dedupeConsecutiveLines(text) {
    var out = [];
    var prev = null;
    var arr = text.split('\n');
    for (var i = 0; i < arr.length; i++) {
      var line = arr[i];
      var key = line.trim().toLowerCase();
      if (key && key === prev) continue;
      out.push(line);
      prev = key;
    }
    return out.join('\n');
  }

  // ─────────────────────────────────────────────────────────────
  // 3. Detección / extracción de ítems
  // ─────────────────────────────────────────────────────────────
  function stripMarker(line) {
    var m;
    // Doble marcador "- 1.- foo" / "• 1) foo": el número manda
    m = line.match(/^[\-\*\+•·●◦▪▫■□–—⇒→➜➝➤]\s+(\d{1,2})\.\-\s+(.+)$/);
    if (m) return { style: 'number', text: m[2] };
    m = line.match(/^[\-\*\+•·●◦▪▫■□–—⇒→➜➝➤]\s+(\d{1,2})[.\)]\s+(.+)$/);
    if (m) return { style: 'number', text: m[2] };
    // "1.- item"
    m = line.match(/^(\d{1,2})\.\-\s+(.+)$/);
    if (m) return { style: 'number', text: m[2] };
    // "(1) item"
    m = line.match(/^\((\d{1,2})\)\s+(.+)$/);
    if (m) return { style: 'number', text: m[2] };
    // "1. item"  /  "1) item"
    m = line.match(/^(\d{1,2})[.\)]\s+(.+)$/);
    if (m) return { style: 'number', text: m[2] };
    // "a) item"
    m = line.match(/^([a-zA-Z])\)\s+(.+)$/);
    if (m) return { style: 'letter', text: m[2] };
    // "i) item" / "IV) item"
    m = line.match(/^[ivxIVX]{1,4}\)\s+(.+)$/);
    if (m) return { style: 'letter', text: m[1] };
    // Viñetas
    m = line.match(/^[\-\*\+•·●◦▪▫■□]\s+(.+)$/);
    if (m) return { style: 'bullet', text: m[1] };
    // Guiones largos
    m = line.match(/^[–—]\s+(.+)$/);
    if (m) return { style: 'bullet', text: m[1] };
    // Flechas
    m = line.match(/^[⇒→➜➝➤]\s+(.+)$/);
    if (m) return { style: 'bullet', text: m[1] };
    return null;
  }

  function explodeInlineEnumerations(text) {
    var t = text;
    // Viñetas unicode → nueva línea "- "
    t = t.replace(/\s*[•·●◦▪▫■□]\s+/g, '\n- ');
    t = t.replace(/\s*[⇒→➜➝➤]\s+/g, '\n- ');
    // Guiones largos como bullet
    t = t.replace(/(^|\n)\s*[–—]\s+/g, '$1- ');
    // Enumeraciones inline: exigir mayúscula después del número para evitar
    // romper referencias legales como "Art. 1. del decreto 22." donde el
    // dígito es parte de la prosa. Las listas reales en español empiezan
    // cada ítem con mayúscula ("1. Atender", "2. Reportar").
    // "texto. 1.- item" → "texto.\n1.- item"
    t = t.replace(/([.;:!?\)\]])\s+(\d{1,2})\.\-\s+(?=[A-ZÁÉÍÓÚÑ])/g, '$1\n$2.- ');
    // "texto. 1. item" / "texto. 1) item"
    t = t.replace(/([.;:!?\)\]])\s+(\d{1,2})[.\)]\s+(?=[A-ZÁÉÍÓÚÑ])/g, '$1\n$2. ');
    // "texto. (1) item"
    t = t.replace(/([.;:!?\)\]])\s+\((\d{1,2})\)\s+(?=[A-ZÁÉÍÓÚÑ])/g, '$1\n$2. ');
    // Secuencia "1. X 2. Y 3. Z" sin puntuación previa fuerte: si hay ≥2 números
    // seguidos de mayúscula, partir cada ocurrencia "<espacio>N. " en nueva línea.
    var seqHits = (t.match(/(^|[\s])(\d{1,2})[.\)]\s+[A-ZÁÉÍÓÚÑ]/g) || []).length;
    if (seqHits >= 2) {
      t = t.replace(/([^\n])\s+(\d{1,2})[.\)]\s+(?=[A-ZÁÉÍÓÚÑ])/g, '$1\n$2. ');
    }
    // "texto. - item"
    t = t.replace(/([.;:!?\)\]])\s+-\s+/g, '$1\n- ');
    // 2+ separadores " - " en prosa → convertir todos.
    // Lookahead para que "A - B - C" cuente 2 (matches solapados), no 1.
    var dashHits = (t.match(/\S\s+-\s+(?=\S)/g) || []).length;
    if (dashHits >= 2) {
      t = t.replace(/(\S)\s+-\s+(?=\S)/g, '$1\n- ');
    }
    return t;
  }

  // Para "Header: item1, item2, item3" genera "Header:\n- item1\n- item2\n- item3"
  // cuando el encabezado es conocido o el contenido se ve claramente listable.
  function explodeInlineListAfterHeader(text) {
    var re = /(^|\n)([A-ZÁÉÍÓÚÑ][^\n:]{2,70}):[ \t]+([^\n]{10,})/g;
    return text.replace(re, function (m, pre, header, rest) {
      var h = header.trim();
      // Si el "header" contiene un punto seguido de letra, es prosa, no encabezado.
      if (/\.\s+\S/.test(h)) return m;
      if (h.length > 60) return m;
      var headerLower = h.toLowerCase();
      var known = HEADER_TOKENS.has(headerLower) || looksLikeListHeader(headerLower);
      if (!known) return m;
      var parts = splitItemsConservatively(rest, true);
      if (!parts || parts.length < 2) return m;
      var lines = parts.map(function (p) { return '- ' + p; }).join('\n');
      return pre + h + ':\n' + lines;
    });
  }

  // Cuando un encabezado conocido aparece a media frase tras punto o punto y coma,
  // lo promovemos a línea propia: "… usuarios. Funciones del cargo:" →
  // "… usuarios.\nFunciones del cargo:".
  var HEADER_INLINE_RE = null;
  function getInlineHeaderRegex() {
    if (HEADER_INLINE_RE) return HEADER_INLINE_RE;
    var tokens = KNOWN_HEADERS.slice()
      .sort(function (a, b) { return b.length - a.length; })
      .map(function (h) { return h.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
    HEADER_INLINE_RE = new RegExp('([.;])[ \\t]+((?:' + tokens.join('|') + ')):(?=[ \\t\\n]|$)', 'gi');
    return HEADER_INLINE_RE;
  }

  function splitOnInlineKnownHeaders(text) {
    return text.replace(getInlineHeaderRegex(), function (m, punct, header) {
      return punct + '\n' + header + ':';
    });
  }

  // En textos scrapeados es común ver encabezados conocidos pegados tras coma/punto
  // o incluso sin ":" final. Este paso los separa a una línea propia para dar
  // jerarquía visual consistente.
  function splitOnKnownHeadersAnyContext(text) {
    var tokens = KNOWN_HEADERS.slice()
      .sort(function (a, b) { return b.length - a.length; })
      .map(function (h) { return h.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
    var re = new RegExp('([.;])\\s+((?:' + tokens.join('|') + '))(?:\\s*:)?(?=\\s+[A-ZÁÉÍÓÚÑ]|\\n|$)', 'gi');
    return text.replace(re, function (m, punct, header) {
      return punct + '\n' + header + ':';
    });
  }

  function looksLikeListHeader(s) {
    return /(competencia|conocimiento|habilidad|requisito|funci[oó]n|especializaci[oó]n|capacitaci[oó]n|experiencia|formaci[oó]n|documento|antecedente|beneficio|perfil)/i.test(s);
  }

  function splitItemsConservatively(rest, forceListy) {
    // Intento 1: punto y coma (señal fuerte)
    var semis = rest.split(/\s*;\s*/).map(trim).filter(Boolean);
    if (semis.length >= 2 && semis.every(function (it) { return it.length <= 140; })) {
      return semis.map(cleanItem);
    }
    // Intento 2: " / " con ≥3 ítems cortos
    var slashes = rest.split(/\s+\/\s+/).map(trim).filter(Boolean);
    if (slashes.length >= 3 && slashes.every(function (it) {
      return it.length <= 80 && it.split(/\s+/).length <= 10;
    })) {
      return slashes.map(cleanItem);
    }
    // Intento 3: comas, con criterio estricto
    var commas = smartCommaSplit(rest);
    if (commas.length >= 3) {
      var allShort = commas.every(function (it) {
        return it.length <= 90 && it.split(/\s+/).length <= 12;
      });
      var noSentences = commas.every(function (it) { return !/\.\s+[A-ZÁÉÍÓÚÑ]/.test(it); });
      var noProse = commas.every(function (it) { return !PROSE_TOKENS.test(it); });
      if (forceListy && allShort && noSentences) return commas.map(cleanItem);
      if (allShort && noSentences && noProse) return commas.map(cleanItem);
    }
    return null;
  }

  // Split por coma que respeta paréntesis: no divide "Curso RCP (ALS, BLS)"
  function smartCommaSplit(s) {
    var out = [];
    var depth = 0;
    var buf = '';
    for (var i = 0; i < s.length; i++) {
      var c = s.charAt(i);
      if (c === '(' || c === '[') depth++;
      else if (c === ')' || c === ']') depth = Math.max(0, depth - 1);
      if (c === ',' && depth === 0) {
        out.push(buf.trim());
        buf = '';
      } else {
        buf += c;
      }
    }
    if (buf.trim()) out.push(buf.trim());
    return out.filter(Boolean);
  }

  function cleanItem(s) {
    return s.replace(/\s*\.\s*$/, '').replace(/^y\s+/i, '').trim();
  }

  function trim(s) { return (s || '').trim(); }

  // ─────────────────────────────────────────────────────────────
  // 4. Detección de encabezados a nivel de línea
  // ─────────────────────────────────────────────────────────────
  function isAllCaps(s) {
    var letters = s.replace(/[^A-Za-zÁÉÍÓÚÑáéíóúñ]/g, '');
    if (letters.length < 3) return false;
    var upper = letters.replace(/[^A-ZÁÉÍÓÚÑ]/g, '').length;
    return (upper / letters.length) >= 0.75;
  }

  function isHeadingColon(s) {
    return /^[^:]{2,80}:\s*$/.test(s) && !/[.!?]$/.test(s);
  }

  function isKnownHeader(s) {
    var clean = s.replace(/[:.\s]+$/, '').trim().toLowerCase();
    return HEADER_TOKENS.has(clean);
  }

  function classifyHeaderTone(text) {
    var low = text.toLowerCase();
    for (var i = 0; i < EXCLUYENTE_NEEDLES.length; i++) {
      if (low.indexOf(EXCLUYENTE_NEEDLES[i]) !== -1) return 'excluyente';
    }
    for (var j = 0; j < DESEABLE_NEEDLES.length; j++) {
      if (low.indexOf(DESEABLE_NEEDLES[j]) !== -1) return 'deseable';
    }
    return 'neutral';
  }

  // "Header: contenido corto" (sin ser un ítem) → separa en dos líneas
  // para que el header pueda clasificarse como heading.
  function liftInlineHeaders(text) {
    var lines = text.split('\n');
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      // No tocar ítems de lista
      if (/^[\-\*\+•·●◦▪▫■□–—⇒→➜➝➤]\s+/.test(line)) { out.push(line); continue; }
      if (/^\d{1,2}[.\)]\s+/.test(line) || /^\d{1,2}\.\-\s+/.test(line)) { out.push(line); continue; }
      var m = line.match(/^([A-ZÁÉÍÓÚÑ][^:]{2,70}):\s+(.{4,})$/);
      if (m && isKnownHeader(m[1] + ':')) {
        out.push(m[1] + ':');
        out.push(m[2]);
      } else {
        out.push(line);
      }
    }
    return out.join('\n');
  }

  // Convierte énfasis markdown o líneas con "encabezado + contenido" en
  // bloques más claros:
  //   "**Formación educacional** Ingeniero..." -> "Formación educacional:\nIngeniero..."
  function liftEmphasizedHeaders(text) {
    var lines = text.split('\n');
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;

      var mdInline = line.match(/^(?:\*\*|__)\s*([^*_:\n]{2,80}?)\s*(?:\*\*|__)\s*:?\s+(.+)$/);
      if (mdInline && (isKnownHeader(mdInline[1]) || looksLikeListHeader(mdInline[1]))) {
        out.push(mdInline[1].trim() + ':');
        out.push(mdInline[2].trim());
        continue;
      }

      var mdOnly = line.match(/^(?:\*\*|__)\s*([^*_:\n]{2,80}?)\s*(?:\*\*|__)\s*:?\s*$/);
      if (mdOnly && (isKnownHeader(mdOnly[1]) || looksLikeListHeader(mdOnly[1]))) {
        out.push(mdOnly[1].trim() + ':');
        continue;
      }

      var plainInline = line.match(/^([A-ZÁÉÍÓÚÑ][^:]{2,70}?)(?:\s*:)?\s{2,}(.+)$/);
      if (plainInline && (isKnownHeader(plainInline[1]) || looksLikeListHeader(plainInline[1]))) {
        out.push(plainInline[1].trim() + ':');
        out.push(plainInline[2].trim());
        continue;
      }

      out.push(line);
    }
    return out.join('\n');
  }

  // ─────────────────────────────────────────────────────────────
  // 5. Splitter: texto normalizado → bloques tipados
  // ─────────────────────────────────────────────────────────────
  function splitIntoStructuredBlocks(text) {
    var blocks = [];
    var lines = text.split('\n').map(trim).filter(Boolean);

    var listBuf = [];
    var listStyle = null;
    var currentTone = 'neutral';

    function flush() {
      if (listBuf.length) {
        blocks.push({
          type: 'list',
          style: listStyle || 'bullet',
          items: listBuf.slice(),
          tone: currentTone
        });
        listBuf = [];
        listStyle = null;
      }
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var marker = stripMarker(line);
      if (marker) {
        if (listStyle && listStyle !== marker.style && listBuf.length) flush();
        listStyle = marker.style;
        listBuf.push(marker.text);
        continue;
      }
      if (isKnownHeader(line) || isHeadingColon(line) || (isAllCaps(line) && line.length <= 80)) {
        flush();
        var clean = line.replace(/[:.\s]+$/, '').trim();
        var tone = classifyHeaderTone(clean);
        currentTone = tone;
        blocks.push({ type: 'heading', text: clean, tone: tone });
        continue;
      }
      flush();
      currentTone = 'neutral';
      blocks.push({ type: 'paragraph', text: line });
    }
    flush();
    return blocks;
  }

  // ─────────────────────────────────────────────────────────────
  // 6. Escape + negrita para "Rótulo:" inline
  // ─────────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function renderInline(s) {
    var esc = escHtml(s);
    esc = esc.replace(
      /(^|[\s\(\[¿¡\.;])([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ0-9][A-Za-zÁÉÍÓÚÑáéíóúñ0-9\s\/\-]{0,58}?):(?=\s|$)/g,
      function (m, pre, label) { return pre + '<strong>' + label + ':</strong>'; }
    );
    // Conserva énfasis inline pero evita negritas heredadas largas e invasivas.
    esc = esc.replace(/(?:\*\*|__)([^*_]{2,120})(?:\*\*|__)/g, '<strong>$1</strong>');
    return esc;
  }

  // ─────────────────────────────────────────────────────────────
  // 7. Renderer de bloques
  // ─────────────────────────────────────────────────────────────
  function renderStructuredContent(blocks, options) {
    options = options || {};
    var out = [];
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (b.type === 'heading') {
        var toneH = b.tone && b.tone !== 'neutral' ? ' rt-heading--' + b.tone : '';
        out.push('<h4 class="rt-heading' + toneH + '">' + escHtml(b.text) + '</h4>');
      } else if (b.type === 'list') {
        var tag = b.style === 'number' ? 'ol' : 'ul';
        var toneL = b.tone && b.tone !== 'neutral' ? ' rt-list--' + b.tone : '';
        var items = b.items.map(function (it) { return '<li>' + renderInline(it) + '</li>'; }).join('');
        out.push('<' + tag + ' class="rt-list' + toneL + '">' + items + '</' + tag + '>');
      } else {
        out.push('<p>' + renderInline(b.text) + '</p>');
      }
    }
    var inner = out.join('');
    var truncAt = options.truncateAt || 900;
    if (options.truncate && estimateLength(blocks) > truncAt) {
      return (
        '<div class="rt-truncate" data-rt-collapsed="true">' +
          '<div class="rt-truncate-inner">' + inner + '</div>' +
          '<button type="button" class="rt-toggle" data-rt-toggle="1">Ver más</button>' +
        '</div>'
      );
    }
    return inner;
  }

  function estimateLength(blocks) {
    var n = 0;
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (b.text) n += b.text.length;
      if (b.items) for (var j = 0; j < b.items.length; j++) n += b.items[j].length;
    }
    return n;
  }

  // ─────────────────────────────────────────────────────────────
  // 8. API pública
  // ─────────────────────────────────────────────────────────────
  function format(rawText, options) {
    if (rawText == null) return '';
    var t = normalizeText(rawText);
    if (!t) return '';
    t = dedupeConsecutiveLines(t);
    t = splitOnInlineKnownHeaders(t);
    t = splitOnKnownHeadersAnyContext(t);
    t = explodeInlineListAfterHeader(t);
    t = explodeInlineEnumerations(t);
    t = liftEmphasizedHeaders(t);
    t = liftInlineHeaders(t);
    t = t.split('\n').map(trim).filter(Boolean).join('\n');
    var blocks = splitIntoStructuredBlocks(t);
    return renderStructuredContent(blocks, options || {});
  }

  // Delegación de eventos para "Ver más / Ver menos"
  function installToggleHandler(root) {
    root = root || document;
    if (root.__rtToggleInstalled) return;
    root.__rtToggleInstalled = true;
    root.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('[data-rt-toggle]');
      if (!btn) return;
      var wrap = btn.closest('.rt-truncate');
      if (!wrap) return;
      var collapsed = wrap.getAttribute('data-rt-collapsed') === 'true';
      wrap.setAttribute('data-rt-collapsed', collapsed ? 'false' : 'true');
      btn.textContent = collapsed ? 'Ver menos' : 'Ver más';
    });
  }

  window.richText = {
    format: format,
    normalize: normalizeText,
    splitBlocks: splitIntoStructuredBlocks,
    installToggleHandler: installToggleHandler
  };

  // Compatibilidad con llamadas existentes: truncado por defecto activado.
  window.formatRichText = function (raw) {
    return format(raw, { truncate: true, truncateAt: 900 });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { installToggleHandler(document); });
  } else {
    installToggleHandler(document);
  }
})();
