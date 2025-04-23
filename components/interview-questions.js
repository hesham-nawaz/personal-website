document.addEventListener('DOMContentLoaded', function() {
  let questions = [];
  let activeFilters = {
    categories: [],
    tags: [],
    difficulties: []
  };
  let searchQuery = '';
  let currentSort = 'date'; // default sort by date
  
  // Update the path to match where you stored the JSON file
  fetch('/components/interview-questions.json')
    .then(response => response.json())
    .then(data => {
      questions = data;
      initializeFilters();
      renderQuestions();
    })
    .catch(error => {
      console.error('Error loading questions:', error);
      document.getElementById('questions-container').innerHTML = 
        '<div class="no-results">Error loading questions. Check the console for details.</div>';
    });
  
  function initializeFilters() {
    // Extract unique categories, tags, and difficulties
    const categories = [...new Set(questions.map(q => q.category))];
    const tags = [...new Set(questions.flatMap(q => q.tags))];
    const difficulties = [...new Set(questions.map(q => q.difficulty))];
    
    // Populate category filters
    const categoryFilters = document.getElementById('category-filters');
    categories.forEach(category => {
      const button = document.createElement('button');
      button.className = 'category-btn';
      button.textContent = category;
      button.addEventListener('click', () => toggleFilter('categories', category, button));
      categoryFilters.appendChild(button);
    });
    
    // Populate tag filters
    const tagFilters = document.getElementById('tag-filters');
    tags.forEach(tag => {
      const button = document.createElement('button');
      button.className = 'subject-btn';
      button.textContent = tag;
      button.addEventListener('click', () => toggleFilter('tags', tag, button));
      tagFilters.appendChild(button);
    });
    
    // Populate difficulty filters
    const difficultyFilters = document.getElementById('difficulty-filters');
    difficulties.forEach(difficulty => {
      const button = document.createElement('button');
      button.className = 'category-btn';
      button.textContent = difficulty;
      button.addEventListener('click', () => toggleFilter('difficulties', difficulty, button));
      difficultyFilters.appendChild(button);
    });
    
    // Set up search
    const searchInput = document.getElementById('question-search');
    searchInput.addEventListener('input', (e) => {
      searchQuery = e.target.value.toLowerCase();
      renderQuestions();
    });
    
    // Set up sorting
    document.getElementById('sort-date').addEventListener('click', () => {
      setSorting('date');
    });
    
    document.getElementById('sort-difficulty').addEventListener('click', () => {
      setSorting('difficulty');
    });
  }
  
  function toggleFilter(filterType, value, buttonElement) {
    if (activeFilters[filterType].includes(value)) {
      // Remove filter
      activeFilters[filterType] = activeFilters[filterType].filter(item => item !== value);
      buttonElement.classList.remove('active');
    } else {
      // Add filter
      activeFilters[filterType].push(value);
      buttonElement.classList.add('active');
    }
    renderQuestions();
  }
  
  function setSorting(sortType) {
    currentSort = sortType;
    
    // Update active button state
    document.querySelectorAll('.sort-btn').forEach(btn => {
      btn.classList.remove('active');
    });
    document.getElementById(`sort-${sortType}`).classList.add('active');
    
    renderQuestions();
  }
  
  function renderQuestions() {
    const container = document.getElementById('questions-container');
    container.innerHTML = '';
    
    // Filter questions
    let filteredQuestions = questions.filter(question => {
      // Apply category filter
      if (activeFilters.categories.length > 0 && !activeFilters.categories.includes(question.category)) {
        return false;
      }
      
      // Apply tag filter
      if (activeFilters.tags.length > 0 && !activeFilters.tags.some(tag => question.tags.includes(tag))) {
        return false;
      }
      
      // Apply difficulty filter
      if (activeFilters.difficulties.length > 0 && !activeFilters.difficulties.includes(question.difficulty)) {
        return false;
      }
      
      // Apply search query
      if (searchQuery && !question.question.toLowerCase().includes(searchQuery) && 
          !question.answer.toLowerCase().includes(searchQuery)) {
        return false;
      }
      
      return true;
    });
    
    // Sort questions
    if (currentSort === 'date') {
      filteredQuestions.sort((a, b) => new Date(b.dateAdded) - new Date(a.dateAdded));
    } else if (currentSort === 'difficulty') {
      const difficultyOrder = { 'beginner': 1, 'intermediate': 2, 'advanced': 3 };
      filteredQuestions.sort((a, b) => difficultyOrder[a.difficulty] - difficultyOrder[b.difficulty]);
    }
    
    // Display questions or "no results" message
    if (filteredQuestions.length === 0) {
      container.innerHTML = '<div class="no-results">No questions match your filters. Try adjusting your criteria.</div>';
      return;
    }
    
    filteredQuestions.forEach(question => {
      const card = document.createElement('div');
      card.className = 'question-card';
      
      const header = document.createElement('div');
      header.className = 'question-header';
      header.innerHTML = `
        <h3>${question.question}</h3>
        <span class="toggle-icon">▼</span>
      `;
      
      const body = document.createElement('div');
      body.className = 'question-body';
      
      // Create difficulty badge
      const difficultyClass = `difficulty-${question.difficulty.toLowerCase()}`;
      
      body.innerHTML = `
        <div class="answer">${question.answer}</div>
        <div class="question-tags">
          ${question.tags.map(tag => `<span>${tag}</span>`).join('')}
        </div>
        <div class="question-meta">
          <span class="difficulty-badge ${difficultyClass}">${question.difficulty}</span>
          <span class="date-added">Added: ${formatDate(question.dateAdded)}</span>
        </div>
      `;
      
      // Toggle answer visibility
      header.addEventListener('click', () => {
        header.classList.toggle('active');
        body.classList.toggle('active');
      });
      
      card.appendChild(header);
      card.appendChild(body);
      container.appendChild(card);
    });
  }
  
  function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  }
});
