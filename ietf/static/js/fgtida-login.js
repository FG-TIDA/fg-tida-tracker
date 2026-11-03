/**
 * Refresh the CSRF token and prevent duplicate login submissions.
*/
async function onLoginSubmit (ev) {
    const form = ev.currentTarget
    if (!form.dataset.csrfUrl) return
    ev.preventDefault()
    if (form.dataset.submitting === 'true') return
    form.dataset.submitting = 'true'
    const submitBtn = document.querySelector('#dt-login-form button[type=submit]')
    if (submitBtn) {
        submitBtn.disabled = true
        submitBtn.innerHTML = 'Signing in...'
    }
    try {
        // A login in another tab rotates the cookie. Fetch a token matching
        // that cookie rather than posting a stale hidden form token.
        const response = await fetch(form.dataset.csrfUrl, {credentials: 'same-origin', cache: 'no-store'})
        if (!response.ok) throw new Error('Unable to refresh sign-in token')
        const data = await response.json()
        if (typeof data.csrfToken !== 'string' || !data.csrfToken) throw new Error('Missing token')
        form.elements.csrfmiddlewaretoken.value = data.csrfToken
        HTMLFormElement.prototype.submit.call(form)
    } catch {
        form.dataset.submitting = 'false'
        document.getElementById('login-refresh-error').hidden = false
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Sign in' }
    }
}

$(function() {
    document.querySelector('#dt-login-form').addEventListener('submit', onLoginSubmit)
})

window.addEventListener('pageshow', () => {
    const form = document.getElementById('dt-login-form')
    if (!form) return
    form.dataset.submitting = 'false'
    const button = form.querySelector('button[type=submit]')
    if (button) { button.disabled = false; button.textContent = 'Sign in' }
})
