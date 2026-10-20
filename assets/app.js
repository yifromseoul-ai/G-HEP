(() => {
  const menuButton = document.querySelector('.menu-toggle');
  const nav = document.querySelector('#main-nav');
  menuButton.addEventListener('click', () => {
    const opened = menuButton.getAttribute('aria-expanded') === 'true';
    menuButton.setAttribute('aria-expanded', String(!opened));
    menuButton.setAttribute('aria-label', opened ? '전체 메뉴 열기' : '전체 메뉴 닫기');
    nav.classList.toggle('open', !opened);
  });
  nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    menuButton.setAttribute('aria-expanded', 'false');
    menuButton.setAttribute('aria-label', '전체 메뉴 열기');
    nav.classList.remove('open');
  }));

  const logo = document.querySelector('.gachon-logo');
  logo.addEventListener('error', () => {
    logo.hidden = true;
    document.querySelector('.brand-divider').hidden = true;
  });

  const guidelineButtons = [...document.querySelectorAll('[data-guideline]')];
  const guidelineCards = [...document.querySelectorAll('.guideline-card')];
  guidelineButtons.forEach(button => button.addEventListener('click', () => {
    guidelineButtons.forEach(item => {
      const selected = item === button;
      item.classList.toggle('active', selected);
      item.setAttribute('aria-pressed', String(selected));
    });
    guidelineCards.forEach(card => {
      card.hidden = button.dataset.guideline !== 'all' && card.dataset.group !== button.dataset.guideline;
    });
  }));

  const trendList = document.querySelector('#trend-list');
  const trendCount = document.querySelector('#trend-count');
  const trendMore = document.querySelector('#trend-more');
  const search = document.querySelector('#trend-search');
  const sort = document.querySelector('#trend-sort');
  const scopeButtons = [...document.querySelectorAll('[data-scope]')];
  const journalButtons = [...document.querySelectorAll('[data-journal]')];
  const dialog = document.querySelector('#article-dialog');
  const closeDialog = document.querySelector('#dialog-close');
  let articles = [];
  let activeScope = 'all';
  let activeJournal = 'all';
  let visibleLimit = 20;
  let lastFocused = null;

  const formatDate = value => value.replaceAll('-', '.');
  const text = (selector, value) => { document.querySelector(selector).textContent = value || '—'; };
  const safeUrl = value => { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : null; } catch { return null; } };

  function openArticle(article, updateHash = true) {
    if (!article) return;
    lastFocused = document.activeElement;
    text('#dialog-journal', article.journal);
    text('#dialog-title', article.title);
    text('#dialog-authors', article.authors);
    text('#dialog-published', formatDate(article.published));
    text('#dialog-abstract', article.abstract_summary || (article.abstract_available
      ? '초록 요약은 아직 검토 중입니다. 아래 원문 초록을 확인해 주세요.'
      : '초록 요약은 아직 검토 중입니다. 공개 서지에서 초록을 확인하지 못했습니다.'));
    const doi = document.querySelector('#dialog-doi');
    doi.replaceChildren();
    if (article.doi) {
      const link = document.createElement('a');
      link.href = `https://doi.org/${encodeURI(article.doi)}`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = article.doi;
      doi.append(link);
    } else doi.textContent = '—';
    document.querySelector('#dialog-source').href = safeUrl(article.source_url) || '#trends';
    const abstractSource = document.querySelector('#dialog-abstract-source');
    const abstractUrl = article.abstract_available ? safeUrl(article.abstract_url || article.source_url) : null;
    abstractSource.hidden = !abstractUrl;
    if (abstractUrl) abstractSource.href = abstractUrl;
    const status = document.querySelector('#dialog-review-status');
    status.hidden = !article.metadata_source?.includes('PubMed 미수록');
    if (updateHash) history.replaceState(null, '', `#trend/${encodeURIComponent(article.id)}`);
    if (!dialog.open) dialog.showModal();
  }

  function createCard(article) {
    const card = document.createElement('article');
    card.className = 'trend-card';
    const top = document.createElement('div');
    top.className = 'trend-card-top';
    const journal = document.createElement('span');
    journal.className = 'trend-meta';
    journal.textContent = article.journal;
    const date = document.createElement('time');
    date.className = 'trend-date';
    date.dateTime = article.published;
    date.textContent = formatDate(article.published);
    top.append(journal);
    if (article.metadata_source?.includes('PubMed 미수록')) {
      const status = document.createElement('span');
      status.className = 'trend-review-status';
      status.textContent = '논문 유형 검토 중';
      top.append(status);
    }
    top.append(date);
    const title = document.createElement('h3');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = article.title;
    button.setAttribute('aria-label', `${article.title} 상세 보기`);
    button.addEventListener('click', () => openArticle(article));
    title.append(button);
    const summary = document.createElement('p');
    summary.className = 'trend-summary';
    summary.textContent = article.abstract_summary || (article.abstract_available
      ? '초록 요약 검토 중 · 상세 보기에서 원문 초록 확인'
      : '초록 요약 검토 중 · 공개 서지에 초록 없음');
    const bottom = document.createElement('div');
    bottom.className = 'trend-card-bottom';
    const author = document.createElement('span');
    author.textContent = article.authors;
    const more = document.createElement('span');
    more.textContent = '상세 보기 ↗';
    bottom.append(author, more);
    card.append(top, title, summary, bottom);
    return card;
  }

  function renderTrends(resetLimit = false) {
    if (resetLimit) visibleLimit = 20;
    const query = search.value.trim().toLocaleLowerCase();
    const visible = articles
      .filter(article => (activeScope === 'all' || article.scope === activeScope) && (activeJournal === 'all' || article.journal === activeJournal))
      .filter(article => !query || [article.title, article.authors, article.journal, article.abstract_summary].some(value => (value || '').toLocaleLowerCase().includes(query)))
      .sort((a, b) => sort.value === 'oldest' ? a.published.localeCompare(b.published) : b.published.localeCompare(a.published));
    trendCount.textContent = `총 ${visible.length}건${visible.length > visibleLimit ? ` · ${visibleLimit}건 표시` : ''}`;
    trendList.replaceChildren();
    trendMore.hidden = visible.length <= visibleLimit;
    if (!visible.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      const strong = document.createElement('strong');
      strong.textContent = articles.length ? '해당 조건의 게시글이 없습니다.' : '등록된 연구동향이 없습니다.';
      const detail = document.createElement('span');
      detail.textContent = articles.length ? '다른 학술지나 검색어를 선택해 보세요.' : '자료가 등록되면 출판일 순으로 표시됩니다.';
      empty.append(strong, detail);
      trendList.append(empty);
      return;
    }
    visible.slice(0, visibleLimit).forEach(article => trendList.append(createCard(article)));
  }

  scopeButtons.forEach(button => button.addEventListener('click', () => {
    activeScope = button.dataset.scope;
    activeJournal = 'all';
    scopeButtons.forEach(item => {
      const selected = item === button;
      item.classList.toggle('active', selected);
      item.setAttribute('aria-pressed', String(selected));
    });
    journalButtons.forEach(item => item.classList.remove('active'));
    renderTrends(true);
  }));
  journalButtons.forEach(button => button.addEventListener('click', () => {
    activeJournal = button.dataset.journal;
    activeScope = 'all';
    scopeButtons.forEach(item => {
      const selected = item.dataset.scope === 'all';
      item.classList.toggle('active', selected);
      item.setAttribute('aria-pressed', String(selected));
    });
    journalButtons.forEach(item => item.classList.toggle('active', item === button));
    renderTrends(true);
    if (window.innerWidth < 850) trendList.scrollIntoView({behavior: 'smooth', block: 'start'});
  }));
  search.addEventListener('input', () => renderTrends(true));
  sort.addEventListener('change', () => renderTrends(true));
  trendMore.addEventListener('click', () => { visibleLimit += 20; renderTrends(); });
  closeDialog.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => {
    if (location.hash.startsWith('#trend/')) history.replaceState(null, '', '#trends');
    if (lastFocused && document.contains(lastFocused)) lastFocused.focus();
  });

  const fetchArticles = path => fetch(path)
    .then(response => { if (!response.ok) throw new Error('Content load failed'); return response.json(); })
    .then(data => { if (!Array.isArray(data)) throw new Error('Invalid content format'); return data; });
  Promise.all([fetchArticles('./content/trends.json'), fetchArticles('./content/auto-trends.json').catch(() => [])])
    .then(([curated, imported]) => {
      const byIdentity = new Map();
      // Reviewed articles take priority over automatically gathered metadata.
      [...imported, ...curated].forEach(item => {
        if (!item || !item.id || !item.title || !item.journal || !item.published || !safeUrl(item.source_url)) return;
        const identity = item.doi ? `doi:${item.doi.toLowerCase().trim()}` : `id:${item.id}`;
        byIdentity.set(identity, item);
      });
      articles = [...byIdentity.values()];
      renderTrends();
      if (location.hash.startsWith('#trend/')) {
        const id = decodeURIComponent(location.hash.slice(7));
        openArticle(articles.find(item => item.id === id), false);
      }
    })
    .catch(() => {
      trendCount.textContent = '자료를 불러올 수 없음';
      trendList.innerHTML = '<p class="empty-state"><strong>연구동향을 불러오지 못했습니다.</strong><span>GitHub Pages 또는 로컬 웹 서버에서 열어 주세요.</span></p>';
    });

  const briefList = document.querySelector('#brief-list');
  const briefCount = document.querySelector('#brief-count');
  const briefDialog = document.querySelector('#brief-dialog');
  const briefClose = document.querySelector('#brief-dialog-close');
  let briefs = [];
  let lastBriefFocus = null;

  function openBrief(item, updateHash = true) {
    lastBriefFocus = document.activeElement;
    text('#brief-dialog-meta', `${formatDate(item.month)} · ${item.category}`);
    text('#brief-dialog-title', item.title);
    text('#brief-dialog-summary', item.summary);
    const sections = document.querySelector('#brief-dialog-sections');
    sections.replaceChildren();
    item.sections.forEach(section => {
      const block = document.createElement('section');
      const heading = document.createElement('h3');
      heading.textContent = section.heading;
      const body = document.createElement('p');
      body.textContent = section.body;
      block.append(heading, body);
      sections.append(block);
    });
    const sources = document.querySelector('#brief-dialog-sources');
    sources.replaceChildren();
    if (item.sources.length) {
      const heading = document.createElement('h3');
      heading.textContent = '근거 자료';
      sources.append(heading);
      item.sources.forEach(source => {
        const url = safeUrl(source.url);
        if (!url) return;
        const link = document.createElement('a');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = source.label || url;
        sources.append(link);
      });
    }
    if (updateHash) history.replaceState(null, '', `#brief/${encodeURIComponent(item.id)}`);
    if (!briefDialog.open) briefDialog.showModal();
  }

  function renderBriefs() {
    briefList.replaceChildren();
    briefCount.textContent = `이슈브리프 ${briefs.length}건`;
    if (!briefs.length) {
      const empty = document.createElement('p');
      empty.className = 'brief-empty';
      empty.textContent = '아직 등록된 브리프가 없습니다.';
      briefList.append(empty);
      return;
    }
    briefs.sort((a, b) => b.month.localeCompare(a.month)).forEach(item => {
      const card = document.createElement('article');
      card.className = 'brief-card';
      const info = document.createElement('div');
      const meta = document.createElement('p');
      meta.textContent = `${formatDate(item.month)} · ${item.category}`;
      const title = document.createElement('h4');
      title.textContent = item.title;
      info.append(meta, title);
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '브리프 보기 ↗';
      button.setAttribute('aria-label', `${item.title} 브리프 보기`);
      button.addEventListener('click', () => openBrief(item));
      card.append(info, button);
      briefList.append(card);
    });
  }
  briefClose.addEventListener('click', () => briefDialog.close());
  briefDialog.addEventListener('click', event => { if (event.target === briefDialog) briefDialog.close(); });
  briefDialog.addEventListener('close', () => {
    if (location.hash.startsWith('#brief/')) history.replaceState(null, '', '#briefs');
    if (lastBriefFocus && document.contains(lastBriefFocus)) lastBriefFocus.focus();
  });
  fetch('./content/briefs.json')
    .then(response => { if (!response.ok) throw new Error('Brief load failed'); return response.json(); })
    .then(data => {
      if (!Array.isArray(data)) throw new Error('Invalid brief format');
      briefs = data.filter(item => item && item.id && item.title && item.month && item.summary && Array.isArray(item.sections) && Array.isArray(item.sources));
      renderBriefs();
      if (location.hash.startsWith('#brief/')) {
        const id = decodeURIComponent(location.hash.slice(7));
        const item = briefs.find(entry => entry.id === id);
        if (item) openBrief(item, false);
      }
    })
    .catch(() => {
      briefCount.textContent = '목록을 불러올 수 없음';
      briefList.innerHTML = '<p class="brief-empty">GitHub Pages 또는 로컬 웹 서버에서 열어 주세요.</p>';
    });
})();
