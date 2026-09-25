document.querySelectorAll('time[datetime]').forEach(time => {
  const date = new Date(time.dateTime);
  if (!Number.isNaN(date.getTime())) time.textContent = date.toLocaleString([], {dateStyle:'full', timeStyle:'short'});
});
const form = document.querySelector('#registration-form');
if (form) form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  const message = document.querySelector('#registration-message');
  button.disabled = true;
  try {
    const response = await fetch(form.dataset.endpoint, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check your name and email and try again.');
    message.textContent = data.message;
    form.reset();
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
});
