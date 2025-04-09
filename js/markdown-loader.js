// Function to fetch and render markdown
async function loadMarkdownArticle(articlePath, containerId) {
  try {
    console.log(`Attempting to load article from: ${articlePath}`);
    
    // Fetch the markdown file
    const response = await fetch(articlePath);
    if (!response.ok) {
      console.error(`Failed to load article: ${response.status} ${response.statusText}`);
      throw new Error(`Failed to load article: ${response.status}`);
    }
    
    const markdownText = await response.text();
    console.log("Article loaded successfully");
    
    // Simple markdown to HTML converter
    // This is a basic implementation - for production, consider using a library like marked.js
    let html = markdownText
      // Convert headers
      .replace(/^# (.*$)/gm, '<h2>$1</h2>')
      .replace(/^## (.*$)/gm, '<h3>$1</h3>')
      .replace(/^### (.*$)/gm, '<h4>$1</h4>')
      
      // Convert paragraphs
      .replace(/^\s*(\n)?(.+)/gm, function(m) {
        return /^<(\/)?(h2|h3|h4|ul|ol|li|blockquote|pre|img)/.test(m) ? m : '<p>' + m + '</p>';
      })
      
      // Convert bold text
      .replace(/\*\*(.*)\*\*/gm, '<strong>$1</strong>')
      
      // Convert italic text
      .replace(/\*(.*)\*/gm, '<em>$1</em>')
      
      // Convert blockquotes
      .replace(/^\> (.*$)/gm, '<blockquote><p>$1</p></blockquote>')
      
      // Convert lists
      .replace(/^\- (.*$)/gm, '<ul><li>$1</li></ul>')
      
      // Fix lists (combine consecutive <ul> elements)
      .replace(/<\/ul>\s*<ul>/g, '');
    
    // Insert the HTML into the container
    document.getElementById(containerId).innerHTML = html;
  } catch (error) {
    console.error('Error loading markdown article:', error);
    document.getElementById(containerId).innerHTML = `
      <p>Error loading article. Please try again later.</p>
      <p><small>Details: ${error.message}</small></p>
    `;
  }
}
