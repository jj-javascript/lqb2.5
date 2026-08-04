(function () {
  'use strict';

  if (!document.querySelector('.bookshelf-page')) {
    return;
  }

  var searchInput = document.getElementById('bookSearch');
  var emptyMsg = document.getElementById('bookshelfEmpty');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.book-card'));
  var curatedCards = cards.filter(function (card) {
    return !card.classList.contains('book-card--sample');
  });

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

  function toggleFlip(card) {
    setFlipped(card, !card.classList.contains('flipped'));
  }

  function closeOtherFlips(activeCard) {
    cards.forEach(function (card) {
      if (card !== activeCard && card.classList.contains('flipped')) {
        setFlipped(card, false);
      }
    });
  }

  cards.forEach(function (card) {
    card.setAttribute('aria-expanded', 'false');

    card.addEventListener('click', function (event) {
      if (event.target.closest('a')) {
        return;
      }
      closeOtherFlips(card);
      toggleFlip(card);
    });

    card.addEventListener('keydown', function (event) {
      if (event.target.closest('a')) {
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        closeOtherFlips(card);
        toggleFlip(card);
      }
      if (event.key === 'Escape' && card.classList.contains('flipped')) {
        setFlipped(card, false);
      }
    });
  });

  document.addEventListener('click', function (event) {
    if (!event.target.closest('.book-card')) {
      cards.forEach(function (card) {
        setFlipped(card, false);
      });
    }
  });

  if (searchInput) {
    searchInput.addEventListener('input', filterBooks);
  }
})();
