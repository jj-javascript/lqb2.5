(function () {
  'use strict';

  if (!document.querySelector('.bookshelf-page')) {
    return;
  }

  var searchInput = document.getElementById('bookSearch');
  var emptyMsg = document.getElementById('bookshelfEmpty');
  var modalBackdrop = document.querySelector('.book-modal-backdrop');
  var modal = document.querySelector('.book-modal');
  var modalContent = document.querySelector('.book-modal-content');
  var modalClose = document.querySelector('.book-modal-close');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.book-card'));
  var curatedCards = cards.filter(function (card) {
    return !card.classList.contains('book-card--sample');
  });
  var activeCard = null;
  var modalTransitionMs = 300;

  function filterBooks() {
    if (!searchInput) return;
    var query = searchInput.value.trim().toLowerCase();
    var visible = 0;

    curatedCards.forEach(function (card) {
      var title = card.getAttribute('data-title') || '';
      var author = card.getAttribute('data-author') || '';
      var match = !query || title.indexOf(query) !== -1 || author.indexOf(query) !== -1;
      card.classList.toggle('hidden', !match);
      if (match) visible += 1;
    });

    if (emptyMsg) {
      emptyMsg.hidden = visible > 0 || !query;
    }
  }

  function setFlipped(card, flipped) {
    card.classList.toggle('flipped', flipped);
    card.setAttribute('aria-expanded', String(flipped));
    var back = card.querySelector('.book-flip-back');
    if (back) {
      back.setAttribute('aria-hidden', String(!flipped));
    }
  }

  function isModalOpen() {
    return modal && modal.classList.contains('active');
  }

  function populateModal(card) {
    var backContent = card.querySelector('.book-back-content');
    if (!backContent || !modalContent) {
      return false;
    }

    modalContent.innerHTML = '';
    modalContent.appendChild(backContent.cloneNode(true));

    var title = card.getAttribute('data-title') || 'Book details';
    if (modal) {
      modal.setAttribute('aria-label', title);
    }

    return true;
  }

  function openModal(card) {
    if (!modal || !modalBackdrop || !modalContent) {
      return;
    }

    if (!populateModal(card)) {
      return;
    }

    if (activeCard && activeCard !== card) {
      setFlipped(activeCard, false);
    }

    closeOtherFlips(card);
    setFlipped(card, true);
    activeCard = card;

    if (isModalOpen()) {
      return;
    }

    modalBackdrop.hidden = false;
    modal.hidden = false;
    document.body.classList.add('bookshelf-modal-open');

    window.requestAnimationFrame(function () {
      modalBackdrop.classList.add('active');
      modal.classList.add('active');
    });

    if (modalClose) {
      modalClose.focus();
    }
  }

  function closeModal() {
    if (!modal || !modalBackdrop || !modalContent) {
      return;
    }

    modalBackdrop.classList.remove('active');
    modal.classList.remove('active');
    document.body.classList.remove('bookshelf-modal-open');

    if (activeCard) {
      setFlipped(activeCard, false);
      activeCard = null;
    }

    window.setTimeout(function () {
      if (!isModalOpen()) {
        modalBackdrop.hidden = true;
        modal.hidden = true;
        modalContent.innerHTML = '';
      }
    }, modalTransitionMs);
  }

  function closeOtherFlips(currentCard) {
    cards.forEach(function (card) {
      if (card !== currentCard && card.classList.contains('flipped')) {
        setFlipped(card, false);
      }
    });
  }

  function handleCardActivate(card) {
    if (activeCard === card && isModalOpen()) {
      closeModal();
      return;
    }

    openModal(card);
  }

  cards.forEach(function (card) {
    card.setAttribute('aria-expanded', 'false');

    card.addEventListener('click', function (event) {
      if (event.target.closest('a')) {
        return;
      }
      handleCardActivate(card);
    });

    card.addEventListener('keydown', function (event) {
      if (event.target.closest('a')) {
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        handleCardActivate(card);
      }
    });
  });

  if (modalBackdrop) {
    modalBackdrop.addEventListener('click', closeModal);
  }

  if (modalClose) {
    modalClose.addEventListener('click', closeModal);
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('.book-card') || event.target.closest('.book-modal')) {
      return;
    }
    if (isModalOpen()) {
      closeModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && isModalOpen()) {
      closeModal();
    }
  });

  if (searchInput) {
    searchInput.addEventListener('input', filterBooks);
  }
})();
