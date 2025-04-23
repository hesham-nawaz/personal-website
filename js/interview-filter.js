document.addEventListener('DOMContentLoaded', function() {
    // Get all filter buttons and interview items
    const categoryButtons = document.querySelectorAll('.category-btn');
    const subjectButtons = document.querySelectorAll('.subject-btn');
    const interviewItems = document.querySelectorAll('.interview-item');
    const sortButton = document.getElementById('sort-toggle');
    
    // Track current filters
    let currentCategory = 'all';
    let currentSubject = 'all';
    let sortNewestFirst = true;
    
    // Add click event listeners to category buttons
    categoryButtons.forEach(button => {
        button.addEventListener('click', function() {
            // Remove active class from all category buttons
            categoryButtons.forEach(btn => btn.classList.remove('active'));
            
            // Add active class to clicked button
            this.classList.add('active');
            
            // Update current category
            currentCategory = this.getAttribute('data-category');
            
            // Apply filters
            applyFilters();
        });
    });
    
    // Add click event listeners to subject buttons
    subjectButtons.forEach(button => {
        button.addEventListener('click', function() {
            // Remove active class from all subject buttons
            subjectButtons.forEach(btn => btn.classList.remove('active'));
            
            // Add active class to clicked button
            this.classList.add('active');
            
            // Update current subject
            currentSubject = this.getAttribute('data-subject');
            
            // Apply filters
            applyFilters();
        });
    });
    
    // Add click event listener to sort button
    if (sortButton) {
        sortButton.addEventListener('click', function() {
            // Toggle sort order
            sortNewestFirst = !sortNewestFirst;
            
            // Update button text and icon
            if (sortNewestFirst) {
                this.innerHTML = '<i class="fas fa-sort-amount-down"></i> Newest First';
            } else {
                this.innerHTML = '<i class="fas fa-sort-amount-up"></i> Oldest First';
            }
            
            // Apply filters and sorting
            applyFilters();
        });
    }
    
    // Function to apply filters and sorting
    function applyFilters() {
        // Get the interview grid
        const interviewGrid = document.querySelector('.interview-grid');
        
        // Create an array from interview items to sort them
        const itemsArray = Array.from(interviewItems);
        
        // Sort items by timestamp
        itemsArray.sort((a, b) => {
            const timestampA = a.getAttribute('data-timestamp');
            const timestampB = b.getAttribute('data-timestamp');
            
            if (sortNewestFirst) {
                return timestampB.localeCompare(timestampA);
            } else {
                return timestampA.localeCompare(timestampB);
            }
        });
        
        // Remove all items from the grid
        interviewItems.forEach(item => {
            item.remove();
        });
        
        // Add sorted and filtered items back to the grid
        itemsArray.forEach(item => {
            const itemCategory = item.getAttribute('data-category');
            const itemSubject = item.getAttribute('data-subject');
            
            // Check if item matches current filters
            const categoryMatch = currentCategory === 'all' || itemCategory === currentCategory;
            const subjectMatch = currentSubject === 'all' || itemSubject === currentSubject;
            
            if (categoryMatch && subjectMatch) {
                item.style.display = 'block';
                interviewGrid.appendChild(item);
            } else {
                item.style.display = 'none';
            }
        });
    }
    
    // Initial application of filters
    applyFilters();
});
