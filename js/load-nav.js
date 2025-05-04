document.addEventListener('DOMContentLoaded', function() {
  fetch('components/navigation.html')
    .then(response => {
      if (!response.ok) {
        throw new Error(`Failed to load navigation: ${response.status}`);
      }
      return response.text();
    })
    .then(data => {
      document.getElementById('nav-placeholder').innerHTML = data;
    })
    .catch(error => {
      console.error('Error loading navigation:', error);
      document.getElementById('nav-placeholder').innerHTML = `
        <nav class="top-nav">
          <ul>
            <li><a href="index.html">home</a></li>
            <li><a href="about.html">about</a></li>
            <li><a href="projects.html">projects</a></li>
           </ul>
        </nav>
      `;
    });
});
