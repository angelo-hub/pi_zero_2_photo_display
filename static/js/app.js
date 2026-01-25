// Photo Frame Web UI - JavaScript

// Auto-refresh status every 30 seconds
let statusRefreshInterval = null;

document.addEventListener('DOMContentLoaded', function() {
    // Initialize time ago displays
    updateAllTimeAgo();
    setInterval(updateAllTimeAgo, 60000);
    
    // Check for auth status on dashboard
    if (document.querySelector('.status-card')) {
        checkAuthStatus();
    }
});

function updateAllTimeAgo() {
    document.querySelectorAll('.time-ago').forEach(el => {
        if (el.dataset.time) {
            el.textContent = formatTimeAgo(new Date(el.dataset.time));
        }
    });
}

function formatTimeAgo(date) {
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);
    
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`;
    return `${Math.floor(diff / 86400)} days ago`;
}

async function checkAuthStatus() {
    try {
        const response = await fetch('/auth/status');
        const data = await response.json();
        
        // Show notification if re-auth needed
        if (data.auth_status === 'requires_2fa') {
            showGlobalNotification(
                '⚠️ iCloud re-authentication required. <a href="/auth">Click here to authenticate</a>',
                'warning'
            );
        }
    } catch (error) {
        console.error('Auth status check failed:', error);
    }
}

function showGlobalNotification(message, type = 'info') {
    // Create notification container if it doesn't exist
    let container = document.getElementById('global-notifications');
    if (!container) {
        container = document.createElement('div');
        container.id = 'global-notifications';
        container.className = 'notifications';
        container.style.top = '1rem';
        container.style.bottom = 'auto';
        document.body.appendChild(container);
    }
    
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.innerHTML = `
        ${message}
        <button onclick="this.parentElement.remove()">×</button>
    `;
    container.appendChild(notification);
}

// Service Worker registration for offline support (optional)
if ('serviceWorker' in navigator) {
    // Could add service worker for PWA support
}
