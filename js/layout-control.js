document.addEventListener('DOMContentLoaded', function() {
    // Get all layout buttons and the interview grid
    const layoutButtons = document.querySelectorAll('.layout-btn');
    const interviewGrid = document.querySelector('.interview-grid');
    
    // Add click event listeners to each layout button
    layoutButtons.forEach(button => {
        button.addEventListener('click', function() {
            // Remove active class from all buttons
            layoutButtons.forEach(btn => btn.classList.remove('active'));
            
            // Add active class to clicked button
            this.classList.add('active');
            
            // Get the number of columns from the data attribute
            const columns = this.getAttribute('data-columns');
            
            // Remove all existing grid column classes
            interviewGrid.classList.remove('grid-columns-1', 'grid-columns-2', 'grid-columns-3');
            
            // Add the appropriate grid column class
            interviewGrid.classList.add(`grid-columns-${columns}`);
        });
    });
});
