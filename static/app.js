/* ============================================================
   TikTok Extractor Pro - Frontend Logic
   ============================================================ */

(() => {
  'use strict';

  // ───── عناصر DOM ─────
  const $ = (sel) => document.querySelector(sel);
  const els = {
    form: $('#extractForm'),
    input: $('#urlInput'),
    btn: $('#extractBtn'),
    btnText: $('#extractBtn .btn-text'),
    btnLoader: $('#extractBtn .btn-loader'),
    err: $('#errorMsg'),
    loading: $('#loadingSection'),
    loadingStage: $('#loadingStage'),
    loadingSteps: $('#loadingSteps'),
    features: $('#featuresSection'),
    result: $('#resultSection'),
    resultKind: $('#resultKind'),
    previewWrap: $('#previewWrap'),
    previewStats: $('#previewStats'),
    downloadGrid: $('#downloadGrid'),
    authorAvatar: $('#authorAvatar'),
    authorNickname: $('#authorNickname'),
    verifiedBadge: $('#verifiedBadge'),
    authorUsername: $('#authorUsername'),
    authorStats: $('#authorStats'),
    descText: $('#descText'),
    hashtagsList: $('#hashtagsList'),
    musicCard: $('#musicCard'),
    musicCover: $('#musicCover'),
    musicTitle: $('#musicTitle'),
    musicAuthor: $('#musicAuthor'),
    musicPlayBtn: $('#musicPlayBtn'),
    musicAudio: $('#musicAudio'),
    statsGrid: $('#statsGrid'),
    metaList: $('#metaList'),
    rawJson: $('#rawJson'),
    copyJsonBtn: $('#copyJsonBtn'),
    downloadJsonBtn: $('#downloadJsonBtn'),
    resetBtn: $('#resetBtn'),
    demoBanner: $('#demoBanner'),
    demoReason: $('#demoReason'),
    demoBtn: $('#demoBtn'),
  };

  let lastResult = null;

  // ───── أدوات مساعدة ─────
  const fmt = (n) => {
    if (n == null || isNaN(n)) return '—';
    n = Number(n);
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1_000)     return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(n);
  };

  const escapeHtml = (s) =>
    String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');

  const proxyUrl = (url, name = '', download = false) => {
    if (!url) return '#';
    const q = new URLSearchParams({ url, name });
    if (download) q.set('download', '1');
    return '/api/proxy?' + q.toString();
  };

  const toast = (msg, kind = '') => {
    const t = document.createElement('div');
    t.className = 'toast ' + kind;
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    setTimeout(() => {
      t.classList.remove('show');
      setTimeout(() => t.remove(), 300);
    }, 2400);
  };

  const setLoading = (loading) => {
    els.btn.disabled = loading;
    els.btnText.hidden = loading;
    els.btnLoader.hidden = !loading;
    els.loading.hidden = !loading;
    els.err.hidden = true;
    els.result.hidden = true;
    if (loading) {
      els.features.hidden = true;
      animateSteps();
    }
  };

  let stepTimer = null;
  const animateSteps = () => {
    const steps = els.loadingSteps.querySelectorAll('.step');
    steps.forEach(s => s.classList.remove('active', 'done'));
    const stages = [
      'تحليل الرابط...',
      'حلّ الرابط المختصر...',
      'جلب صفحة TikTok...',
      'استخراج بيانات JSON...',
      'تحويل وتنسيق البيانات...',
    ];
    let i = 0;
    stepTimer = setInterval(() => {
      if (i > 0) steps[i - 1]?.classList.replace('active', 'done');
      if (i >= steps.length) {
        clearInterval(stepTimer);
        return;
      }
      steps[i].classList.add('active');
      els.loadingStage.textContent = stages[i] || 'جاري المعالجة...';
      i++;
    }, 600);
  };

  // ───── الأيقونات ─────
  const ICONS = {
    heart: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 21s-7-4.35-9.5-8.5C.5 9 2 5 5.5 5c2 0 3.5 1 4.5 2.5C11 6 12.5 5 14.5 5 18 5 19.5 9 17.5 12.5 19 16.65 12 21 12 21z"/></svg>',
    comment: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M21 6h-2v9H6v2c0 .55.45 1 1 1h11l4 4V7c0-.55-.45-1-1-1zm-4 6V3c0-.55-.45-1-1-1H3c-.55 0-1 .45-1 1v14l4-4h10c.55 0 1-.45 1-1z"/></svg>',
    share: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z"/></svg>',
    eye: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>',
    bookmark: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>',
    music: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>',
    image: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>',
    video: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/></svg>',
    link: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/></svg>',
  };

  // ───── عرض النتائج ─────
  const KIND_LABELS = {
    video: '🎬 فيديو',
    photo: '📷 صور',
    note: '📝 ملاحظة',
    live: '🔴 بث مباشر',
    profile: '👤 ملف شخصي',
    short: '🔗 رابط مختصر',
    unknown: '❓ غير معروف',
  };

  const renderPreview = (data) => {
    els.previewWrap.innerHTML = '';
    const v = data.video || {};
    if (data.kind === 'photo' && (data.images || []).length) {
      // معرض صور
      const img = document.createElement('img');
      img.src = data.images[0];
      img.alt = data.title || '';
      img.loading = 'lazy';
      els.previewWrap.appendChild(img);
    } else if (v.play_url) {
      // فيديو (نستخدم proxy لتفادي CORS)
      const video = document.createElement('video');
      video.src = proxyUrl(v.play_url);
      video.poster = v.cover ? proxyUrl(v.cover) : '';
      video.controls = true;
      video.preload = 'metadata';
      video.playsInline = true;
      els.previewWrap.appendChild(video);
    } else if (v.cover) {
      const img = document.createElement('img');
      img.src = proxyUrl(v.cover);
      img.alt = data.title || '';
      img.loading = 'lazy';
      els.previewWrap.appendChild(img);
    } else {
      const ph = document.createElement('div');
      ph.className = 'preview-placeholder';
      ph.textContent = 'لا توجد معاينة متاحة';
      els.previewWrap.appendChild(ph);
    }

    // إحصائيات سريعة على المعاينة
    const s = data.stats || {};
    const items = [];
    if (s.play_count != null) items.push(`<span class="stat">${ICONS.eye} ${fmt(s.play_count)}</span>`);
    if (s.digg_count != null) items.push(`<span class="stat">${ICONS.heart} ${fmt(s.digg_count)}</span>`);
    if (s.comment_count != null) items.push(`<span class="stat">${ICONS.comment} ${fmt(s.comment_count)}</span>`);
    if (s.share_count != null) items.push(`<span class="stat">${ICONS.share} ${fmt(s.share_count)}</span>`);
    els.previewStats.innerHTML = items.join('');
  };

  const renderDownloads = (data) => {
    const items = [];
    const v = data.video || {};
    const m = data.music || {};

    // فيديو رئيسي
    if (v.play_url) {
      items.push(`<a class="download-btn primary" href="${proxyUrl(v.play_url, 'tiktok_video.mp4', true)}" target="_blank" rel="noopener">
        ${ICONS.download}
        <span class="label">تحميل الفيديو<small>${v.format || 'MP4'}${v.width ? ' • ' + v.width + '×' + v.height : ''}</small></span>
      </a>`);
    }
    // صورة الغلاف
    if (v.cover) {
      items.push(`<a class="download-btn" href="${proxyUrl(v.cover, 'cover.jpg', true)}" target="_blank" rel="noopener">
        ${ICONS.image}
        <span class="label">الغلاف<small>JPG</small></span>
      </a>`);
    }
    // غلاف متحرك
    if (v.dynamic_cover) {
      items.push(`<a class="download-btn" href="${proxyUrl(v.dynamic_cover, 'cover.gif', true)}" target="_blank" rel="noopener">
        ${ICONS.image}
        <span class="label">غلاف GIF<small>متحرك</small></span>
      </a>`);
    }
    // موسيقى
    if (m.play_url) {
      items.push(`<a class="download-btn" href="${proxyUrl(m.play_url, 'audio.mp3', true)}" target="_blank" rel="noopener">
        ${ICONS.music}
        <span class="label">الموسيقى<small>MP3</small></span>
      </a>`);
    }
    // صور المنشور
    if ((data.images || []).length > 1) {
      items.push(`<a class="download-btn" href="${proxyUrl(data.images[0], 'image_1.jpg', true)}" target="_blank" rel="noopener">
        ${ICONS.image}
        <span class="label">صور المنشور<small>${data.images.length} صور</small></span>
      </a>`);
    }
    els.downloadGrid.innerHTML = items.join('') || '<p style="color:var(--text-mute);font-size:13px;grid-column:1/-1;text-align:center;padding:14px;">لا توجد روابط تحميل متاحة</p>';
  };

  const renderAuthor = (data) => {
    const a = data.author || {};
    if (a.avatar) {
      els.authorAvatar.src = proxyUrl(a.avatar, 'avatar.jpg');
      els.authorAvatar.style.display = 'block';
    } else {
      els.authorAvatar.style.display = 'none';
    }
    els.authorNickname.textContent = a.nickname || a.unique_id || '—';
    els.verifiedBadge.hidden = !a.verified;

    if (a.unique_id) {
      els.authorUsername.textContent = '@' + a.unique_id;
      els.authorUsername.href = `https://www.tiktok.com/@${a.unique_id}`;
    } else {
      els.authorUsername.textContent = '';
    }

    const stats = [];
    if (a.follower_count != null) stats.push(`<span class="stat">المتابعون <strong>${fmt(a.follower_count)}</strong></span>`);
    if (a.following_count != null) stats.push(`<span class="stat">يتابع <strong>${fmt(a.following_count)}</strong></span>`);
    if (a.like_count != null) stats.push(`<span class="stat">إعجابات <strong>${fmt(a.like_count)}</strong></span>`);
    if (a.video_count != null) stats.push(`<span class="stat">فيديوهات <strong>${fmt(a.video_count)}</strong></span>`);
    els.authorStats.innerHTML = stats.join('');
  };

  const renderDesc = (data) => {
    const desc = (data.description || data.title || '').trim();
    els.descText.innerHTML = escapeHtml(desc).replace(
      /(https?:\/\/[^\s]+)/g,
      '<a href="$1" target="_blank" rel="noopener" style="color:var(--accent-2)">$1</a>'
    );
    els.hashtagsList.innerHTML = (data.hashtags || [])
      .map(h => `<a class="hashtag" href="https://www.tiktok.com/tag/${encodeURIComponent(h)}" target="_blank" rel="noopener">#${escapeHtml(h)}</a>`)
      .join('');
  };

  const renderMusic = (data) => {
    const m = data.music || {};
    if (!m.title && !m.play_url) {
      els.musicCard.hidden = true;
      return;
    }
    els.musicCard.hidden = false;
    els.musicTitle.textContent = m.title || '—';
    els.musicAuthor.textContent = m.author || '';
    if (m.cover) {
      els.musicCover.src = proxyUrl(m.cover, 'music.jpg');
      els.musicCover.style.display = 'block';
    } else {
      els.musicCover.style.display = 'none';
    }
    if (m.play_url) {
      els.musicAudio.src = proxyUrl(m.play_url);
      els.musicPlayBtn.onclick = () => {
        if (els.musicAudio.paused) {
          els.musicAudio.play();
          els.musicPlayBtn.classList.add('playing');
        } else {
          els.musicAudio.pause();
          els.musicPlayBtn.classList.remove('playing');
        }
      };
      els.musicAudio.onended = () => els.musicPlayBtn.classList.remove('playing');
    }
  };

  const renderStats = (data) => {
    const s = data.stats || {};
    const blocks = [
      { v: s.play_count,    l: 'مشاهدة',  icon: 'eye' },
      { v: s.digg_count,    l: 'إعجاب',   icon: 'heart' },
      { v: s.comment_count, l: 'تعليق',   icon: 'comment' },
      { v: s.share_count,   l: 'مشاركة',  icon: 'share' },
      { v: s.collect_count, l: 'مفضلة',   icon: 'bookmark' },
    ];
    els.statsGrid.innerHTML = blocks
      .map(b => `<div class="stat-block">
        <div class="value">${fmt(b.v)}</div>
        <div class="label">${b.l}</div>
      </div>`)
      .join('');
  };

  const renderMeta = (data) => {
    const v = data.video || {};
    const a = data.author || {};
    const items = [
      ['النوع', KIND_LABELS[data.kind] || data.kind],
      ['معرّف الفيديو', data.content_id],
      ['تاريخ الإنشاء', data.create_time ? new Date(data.create_time * 1000).toLocaleString('ar') : null],
      ['sec_uid', a.sec_uid],
      ['user_id', a.user_id],
      ['المدة', v.duration ? v.duration + ' ثانية' : null],
      ['الأبعاد', v.width && v.height ? `${v.width}×${v.height}` : null],
      ['النسبة', v.ratio],
      ['الصيغة', v.format],
      ['مصادر البيانات', (data.raw_keys || []).join(', ')],
      ['الرابط النهائي', data.final_url],
    ].filter(([k, v]) => v != null && v !== '');
    els.metaList.innerHTML = items
      .map(([k, v]) => `<div class="item"><span class="k">${escapeHtml(k)}</span><span class="v" title="${escapeHtml(String(v))}">${escapeHtml(String(v))}</span></div>`)
      .join('');
  };

  const renderResult = (data) => {
    lastResult = data;
    els.resultKind.textContent = KIND_LABELS[data.kind] || data.kind;
    // إظهار/إخفاء لافتة الوضع التجريبي
    const isDemo = (data.raw_keys || []).includes('demo_mode') ||
                   (data.meta_tags || {})._demo_mode === 'true';
    if (isDemo) {
      els.demoBanner.hidden = false;
      const meta = data.meta_tags || {};
      let reason = 'الخادم محجوب جغرافياً من TikTok — البيانات أدناه تجريبية لأغراض العرض. ';
      reason += 'الكود يدعم الرابط بشكل صحيح، وعند النشر على خادم في منطقة مدعومة سيتم استخراج بيانات حقيقية تلقائياً.';
      if (meta._real_error) {
        reason += ' (سبب التراجع: ' + meta._real_error + ')';
      }
      els.demoReason.textContent = reason;
    } else {
      els.demoBanner.hidden = true;
    }
    renderPreview(data);
    renderDownloads(data);
    renderAuthor(data);
    renderDesc(data);
    renderMusic(data);
    renderStats(data);
    renderMeta(data);
    els.rawJson.textContent = JSON.stringify(data, null, 2);
    els.loading.hidden = true;
    els.result.hidden = false;
    els.result.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // ───── إرسال الطلب ─────
  const submit = async (url, forceDemo = false) => {
    if (!url || !url.trim()) {
      toast('الرجاء إدخال رابط', 'error');
      return;
    }
    setLoading(true);
    try {
      const body = forceDemo
        ? JSON.stringify({ url: url.trim(), demo: true })
        : JSON.stringify({ url: url.trim() });
      const r = await fetch('/api/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      const data = await r.json();
      if (stepTimer) clearInterval(stepTimer);
      els.loading.hidden = true;

      if (!data.success) {
        els.err.hidden = false;
        els.err.textContent = '';
        els.err.appendChild(document.createTextNode(data.error || 'فشل الاستخراج'));
        els.features.hidden = false;
        return;
      }
      renderResult(data);
      // رسالة مناسبة حسب الوضع
      const isDemo = (data.raw_keys || []).includes('demo_mode') ||
                     (data.meta_tags || {})._demo_mode === 'true';
      if (isDemo) {
        toast('وضع العرض التجريبي - البيانات لأغراض العرض', 'success');
      } else {
        toast('تم الاستخراج بنجاح', 'success');
      }
    } catch (e) {
      if (stepTimer) clearInterval(stepTimer);
      els.loading.hidden = true;
      els.err.hidden = false;
      els.err.textContent = '';
      els.err.appendChild(document.createTextNode('خطأ شبكي: ' + e.message));
      els.features.hidden = false;
    }
  };

  // ───── ربط الأحداث ─────
  els.form.addEventListener('submit', (e) => {
    e.preventDefault();
    submit(els.input.value);
  });

  document.querySelectorAll('.hint-chip[data-url]').forEach(chip => {
    chip.addEventListener('click', () => {
      els.input.value = chip.dataset.url;
      els.input.focus();
    });
  });

  // زر العرض التجريبي الفوري
  els.demoBtn.addEventListener('click', () => {
    const url = els.input.value.trim() ||
      'https://www.tiktok.com/@tiktok/video/7106594312292453675';
    submit(url, true);
  });

  els.resetBtn.addEventListener('click', () => {
    els.result.hidden = true;
    els.features.hidden = false;
    els.input.value = '';
    els.input.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  els.copyJsonBtn.addEventListener('click', async () => {
    if (!lastResult) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(lastResult, null, 2));
      toast('تم نسخ JSON', 'success');
    } catch {
      toast('تعذّر النسخ', 'error');
    }
  });

  els.downloadJsonBtn.addEventListener('click', () => {
    if (!lastResult) return;
    const blob = new Blob([JSON.stringify(lastResult, null, 2)],
                          { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tiktok_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast('تم تحميل الملف', 'success');
  });

  // لصق تلقائي من الحافظة عند التركيز لأول مرة
  els.input.addEventListener('focus', async () => {
    if (els.input.value) return;
    try {
      const text = await navigator.clipboard.readText();
      if (text && /tiktok\.com|tiktokv\.com/i.test(text)) {
        els.input.value = text.trim();
        toast('تم لصق الرابط من الحافظة');
      }
    } catch { /* ignore */ }
  });

  // اختصارات لوحة المفاتيح
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !els.result.hidden) {
      els.resetBtn.click();
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      els.form.requestSubmit();
    }
  });

  console.log('%cTikTok Extractor Pro', 'font-size:24px;font-weight:bold;color:#ff2d55');
  console.log('%cجاهز للاستخدام — الصق رابط TikTok بأي صيغة', 'color:#25f4ee');
})();


/* ============================================================
   v4.4 — Database Tab Logic
   ============================================================ */
(() => {
  'use strict';

  const API_BASE = window.location.origin;
  const $$ = (s, root=document) => Array.from(root.querySelectorAll(s));

  // ───── تبديل التبويبات ─────
  const tabs = $$('.nav-tab');
  const extractSection = document.querySelector('.input-card')?.parentElement;
  const dbSection = document.getElementById('databaseSection');

  // إخفاء كل أقسام الاستخراج افتراضياً ما عدا أول واحد
  const extractSections = [
    document.querySelector('.input-card'),
    document.getElementById('loadingSection'),
    document.getElementById('resultSection'),
    document.getElementById('featuresSection'),
  ].filter(Boolean);

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      tabs.forEach(t => t.classList.toggle('active', t === tab));
      if (target === 'extract') {
        extractSections.forEach(s => s.hidden = false);
        if (dbSection) dbSection.hidden = true;
      } else if (target === 'database') {
        extractSections.forEach(s => s.hidden = true);
        if (dbSection) dbSection.hidden = false;
        loadUsers();
      }
    });
  });

  // ───── عناصر DOM لقاعدة البيانات ─────
  const dbEls = {
    list: document.getElementById('dbUsersList'),
    detail: document.getElementById('dbUserDetail'),
    detailContent: document.getElementById('dbUserDetailContent'),
    backBtn: document.getElementById('dbBackBtn'),
    refreshBtn: document.getElementById('dbRefreshBtn'),
    syncBtn: document.getElementById('dbSyncBtn'),
    search: document.getElementById('dbSearch'),
    liveOnly: document.getElementById('dbLiveOnly'),
    syncResult: document.getElementById('dbSyncResult'),
    statTotal: document.getElementById('dbStatTotal'),
    statLive: document.getElementById('dbStatLive'),
    statStreams: document.getElementById('dbStatStreams'),
    statVerified: document.getElementById('dbStatVerified'),
    usersBadge: document.getElementById('usersBadge'),
  };

  let searchTimer = null;

  // ───── تحميل قائمة المستخدمين ─────
  async function loadUsers() {
    if (!dbEls.list) return;
    dbEls.list.innerHTML = '<div class="db-empty">⏳ جاري التحميل...</div>';
    try {
      const params = new URLSearchParams();
      if (dbEls.search.value.trim()) params.set('search', dbEls.search.value.trim());
      if (dbEls.liveOnly.checked) params.set('live_only', 'true');
      params.set('limit', '200');

      const resp = await fetch(`${API_BASE}/api/users?${params}`);
      const data = await resp.json();

      if (!data.success || !data.users?.length) {
        dbEls.list.innerHTML = '<div class="db-empty">لا يوجد مستخدمون محفوظون. استخرج رابطاً ليبدأ الحفظ التلقائي.</div>';
        updateStats(0, 0, 0, 0);
        return;
      }

      // إحصائيات
      const total = data.total_users || data.users.length;
      const live = data.users.filter(u => u.latest_is_live).length;
      const streams = data.users.reduce((s, u) => s + (u.streams_detected || 0), 0);
      const verified = data.users.filter(u => u.verified).length;
      updateStats(total, live, streams, verified);

      // عرض البطاقات
      dbEls.list.innerHTML = data.users.map(u => renderUserCard(u)).join('');

      // ربط الأحداث
      $$('.db-user-card', dbEls.list).forEach(card => {
        card.addEventListener('click', () => {
          const uid = card.dataset.uid;
          if (uid) showUserDetail(uid);
        });
      });
    } catch (e) {
      dbEls.list.innerHTML = `<div class="db-empty">❌ فشل التحميل: ${e.message}</div>`;
    }
  }

  function updateStats(total, live, streams, verified) {
    if (dbEls.statTotal) dbEls.statTotal.textContent = total.toLocaleString('en-US');
    if (dbEls.statLive) dbEls.statLive.textContent = live.toLocaleString('en-US');
    if (dbEls.statStreams) dbEls.statStreams.textContent = streams.toLocaleString('en-US');
    if (dbEls.statVerified) dbEls.statVerified.textContent = verified.toLocaleString('en-US');
    if (dbEls.usersBadge) {
      if (total > 0) {
        dbEls.usersBadge.textContent = total > 99 ? '99+' : total;
        dbEls.usersBadge.hidden = false;
      } else {
        dbEls.usersBadge.hidden = true;
      }
    }
  }

  function renderUserCard(u) {
    const isLive = u.latest_is_live;
    const avatar = u.avatar
      ? `<img src="${u.avatar}" alt="" onerror="this.style.display='none'">`
      : `<div style="display:flex;align-items:center;justify-content:center;color:#6b6b85;font-size:20px;">@</div>`;
    const verified = u.verified ? '<span class="verified-tick">✓</span>' : '';
    const influence = u.latest_influence_score ? `${u.latest_influence_score}/100` : '—';
    const followers = u.follower_count ? formatNum(u.follower_count) : '—';
    const viewers = u.latest_viewer_count ? formatNum(u.latest_viewer_count) : '—';
    const streams = u.streams_detected || 0;
    const appearances = u.appearance_count || 0;

    return `
      <div class="db-user-card ${isLive ? 'live' : ''}" data-uid="${escapeHtml(u.unique_id)}">
        <div class="db-user-avatar">${avatar}</div>
        <div class="db-user-info">
          <div class="db-user-name">
            ${isLive ? '<span class="db-live-dot"></span>' : ''}
            ${escapeHtml(u.nickname || u.unique_id || 'مستخدم')}
            ${verified}
          </div>
          <div class="db-user-uid">@${escapeHtml(u.unique_id || 'unknown')}</div>
          <div class="db-user-meta">
            <span>👥 <strong>${followers}</strong></span>
            <span>👀 <strong>${viewers}</strong></span>
            <span>🎬 <strong>${streams}</strong></span>
            <span>⭐ <strong>${influence}</strong></span>
            <span>🔁 <strong>${appearances}</strong></span>
          </div>
        </div>
      </div>
    `;
  }

  // ───── عرض تفاصيل مستخدم ─────
  async function showUserDetail(uid) {
    if (!dbEls.detail || !dbEls.detailContent) return;
    dbEls.list.hidden = true;
    dbEls.detail.hidden = false;
    dbEls.detailContent.innerHTML = '<div class="db-empty">⏳ جاري التحميل...</div>';

    try {
      const resp = await fetch(`${API_BASE}/api/users/${encodeURIComponent(uid)}`);
      const data = await resp.json();
      if (!data.success) throw new Error(data.error || 'User not found');
      dbEls.detailContent.innerHTML = renderUserDetail(data);
    } catch (e) {
      dbEls.detailContent.innerHTML = `<div class="db-empty">❌ ${e.message}</div>`;
    }
  }

  function renderUserDetail(d) {
    const profile = d.profile || {};
    const avatar = profile.avatar
      ? `<img src="${profile.avatar}" alt="" onerror="this.style.display='none'">`
      : '';
    const verified = profile.verified ? '<span class="verified-tick">✓ موثّق</span>' : '';
    const streams = d.stream_history || [];
    const fans = (d.top_fans_seen || []).slice(-20).reverse();
    const snapshots = d.snapshots || [];
    const stats = d.stream_stats || {};
    const latest = snapshots[snapshots.length - 1] || {};

    return `
      <div class="db-user-detail-header">
        ${avatar ? `<div>${avatar}</div>` : ''}
        <div>
          <h2 style="margin:0;font-size:20px;">${escapeHtml(profile.nickname || d.unique_id)}</h2>
          <div style="color:var(--text-mute);font-family:var(--font-mono);">@${escapeHtml(d.unique_id || '')}</div>
          <div style="margin-top:6px;">${verified}</div>
          ${profile.signature ? `<div style="color:var(--text-dim);font-size:13px;margin-top:6px;">${escapeHtml(profile.signature)}</div>` : ''}
        </div>
      </div>

      <div class="db-detail-stats">
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${formatNum(profile.follower_count || 0)}</div>
          <div class="db-detail-stat-label">متابع</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${formatNum(profile.following_count || 0)}</div>
          <div class="db-detail-stat-label">يتابع</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${d.appearance_count || 0}</div>
          <div class="db-detail-stat-label">ظهور</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${streams.length}</div>
          <div class="db-detail-stat-label">بثوث مسجّلة</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${snapshots.length}</div>
          <div class="db-detail-stat-label">لقطات</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${latest.influence_score || '—'}</div>
          <div class="db-detail-stat-label">درجة التأثير</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${latest.trust_score || '—'}</div>
          <div class="db-detail-stat-label">درجة الثقة</div>
        </div>
        <div class="db-detail-stat">
          <div class="db-detail-stat-value">${stats.total_stream_time_hours || 0}h</div>
          <div class="db-detail-stat-label">ساعات بث</div>
        </div>
      </div>

      <div style="color:var(--text-dim);font-size:12px;margin-bottom:16px;">
        أول ظهور: ${d.first_seen || '—'} | آخر ظهور: ${d.last_seen || '—'}
      </div>

      ${streams.length ? `
        <div class="db-section-title">🎬 سجل البثوث (${streams.length})</div>
        <div class="db-streams-timeline">
          ${streams.slice(-15).reverse().map(s => renderStreamItem(s)).join('')}
        </div>
      ` : ''}

      ${fans.length ? `
        <div class="db-section-title">💎 آخر الداعمين (${fans.length})</div>
        <div class="db-fans-list">
          ${fans.map(f => `
            <div class="db-fan-item">
              <span class="db-fan-name">@${escapeHtml(f.unique_id || 'unknown')}</span>
              <span class="db-fan-amount">${formatNum(f.amount || 0)} 💎</span>
            </div>
          `).join('')}
        </div>
      ` : ''}

      ${snapshots.length ? `
        <div class="db-section-title">📈 آخر اللقطات (${snapshots.length})</div>
        <div class="db-streams-timeline">
          ${snapshots.slice(-10).reverse().map(s => `
            <div class="db-stream-item ${s.is_live ? 'live-now' : ''}">
              <span class="db-stream-time">${(s.timestamp || '').substring(0, 19)}</span>
              <span class="db-stream-meta">
                ${s.is_live ? '<span class="db-live-dot"></span>' : ''}
                👥 ${formatNum(s.follower_count || 0)} |
                👀 ${formatNum(s.viewer_count || 0)} |
                🎬 ${formatNum(s.view_count || 0)}
              </span>
              <span class="db-stream-viewers">${s.influence_score ? `⭐ ${s.influence_score}` : ''}</span>
            </div>
          `).join('')}
        </div>
      ` : ''}
    `;
  }

  function renderStreamItem(s) {
    const isLive = s.last_seen_epoch && (Date.now() / 1000 - s.last_seen_epoch < 600);
    const startedAt = s.started_at_iso ? new Date(s.started_at_iso).toLocaleString('ar-EG') : 'غير معروف';
    const lastSeen = s.last_seen_at ? new Date(s.last_seen_at).toLocaleString('ar-EG') : '—';
    const duration = (s.started_at && s.last_seen_epoch)
      ? `${Math.round((s.last_seen_epoch - s.started_at) / 60)} دقيقة`
      : '—';

    return `
      <div class="db-stream-item ${isLive ? 'live-now' : ''}">
        <span class="db-stream-time">${startedAt}</span>
        <span class="db-stream-meta">
          ${isLive ? '<span class="db-live-dot"></span>بث مباشر الآن' : `استمر: ${duration}`}
          <br>Room: ${escapeHtml(s.room_id || '—')}
        </span>
        <span class="db-stream-viewers">👥 ${formatNum(s.peak_viewer_count || 0)}</span>
      </div>
    `;
  }

  // ───── المزامنة مع GitHub ─────
  async function syncToGitHub() {
    if (!dbEls.syncBtn) return;
    dbEls.syncBtn.disabled = true;
    dbEls.syncBtn.innerHTML = '<span>⏳ جاري المزامنة...</span>';
    dbEls.syncResult.hidden = false;
    dbEls.syncResult.className = 'db-sync-result';
    dbEls.syncResult.textContent = '⏳ جاري رفع الملفات إلى GitHub...';

    try {
      const resp = await fetch(`${API_BASE}/api/sync-db`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ commit_message: `sync users DB - ${new Date().toISOString()}` }),
      });
      const data = await resp.json();

      if (data.success) {
        dbEls.syncResult.className = 'db-sync-result success';
        dbEls.syncResult.innerHTML = `
          ✅ <strong>تمت المزامنة بنجاح!</strong><br>
          📁 الملفات المرفوعة: ${data.pushed_files}<br>
          🔗 <a href="${data.commit_url || '#'}" target="_blank" style="color:var(--accent-2);">عرض الـ commit على GitHub</a>
        `;
      } else {
        dbEls.syncResult.className = 'db-sync-result error';
        dbEls.syncResult.innerHTML = `❌ <strong>فشلت المزامنة:</strong> ${escapeHtml(data.error || 'unknown')}<br>${data.hint ? `ℹ️ ${escapeHtml(data.hint)}` : ''}`;
      }
    } catch (e) {
      dbEls.syncResult.className = 'db-sync-result error';
      dbEls.syncResult.innerHTML = `❌ خطأ: ${escapeHtml(e.message)}`;
    } finally {
      dbEls.syncBtn.disabled = false;
      dbEls.syncBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg> مزامنة GitHub';
    }
  }

  // ───── أدوات مساعدة ─────
  function formatNum(n) {
    if (n === null || n === undefined) return '0';
    n = Number(n);
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return String(n);
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // ───── ربط الأحداث ─────
  if (dbEls.refreshBtn) dbEls.refreshBtn.addEventListener('click', loadUsers);
  if (dbEls.syncBtn) dbEls.syncBtn.addEventListener('click', syncToGitHub);
  if (dbEls.backBtn) dbEls.backBtn.addEventListener('click', () => {
    dbEls.detail.hidden = true;
    dbEls.list.hidden = false;
  });
  if (dbEls.search) {
    dbEls.search.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(loadUsers, 300);
    });
  }
  if (dbEls.liveOnly) dbEls.liveOnly.addEventListener('change', loadUsers);

  // تحميل المستخدمين عند فتح التبويب لأول مرة (لتحديث الـ badge)
  setTimeout(() => {
    fetch(`${API_BASE}/api/users?limit=1`)
      .then(r => r.json())
      .then(data => {
        if (data.total_users > 0 && dbEls.usersBadge) {
          dbEls.usersBadge.textContent = data.total_users > 99 ? '99+' : data.total_users;
          dbEls.usersBadge.hidden = false;
        }
      })
      .catch(() => {});
  }, 2000);

  console.log('%c[v4.4] Database tab initialized', 'color:#25f4ee');
})();
