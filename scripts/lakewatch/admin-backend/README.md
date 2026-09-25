# Lake Watch publishing backend

`lake-admin.html` signs editors in with Google. This Apps Script web app is
the only thing that can change the site: it checks each request's Google ID
token, checks the email against a list you keep in Script Properties, and then
makes the change on GitHub with a token that stays on Google's servers.

```
browser ── Google sign-in ──▶ ID token (1 hour, names the email)
browser ── ID token + story ─▶ Apps Script ── checks token + email list
                                           └─ GitHub token ─▶ branch → PR → merge
```

Nothing in `Code.gs` is secret. The GitHub token and the email lists live in
Script Properties, so they never appear in this public repository.

## One-time setup

### 1. Google sign-in client ID

1. Go to <https://console.cloud.google.com/> and create a project named
   `Lake Watch`.
2. Open **Google Auth Platform** (it may be listed as **APIs & Services → OAuth
   consent screen**).
3. Fill in **Branding**: the app name, your support email, and the authorised
   domain `friendsoflakemattoon.org`.
4. Set **Audience** to **External**, then **Publish app**. Sign-in that asks only
   for name and email needs no Google review.
5. Under **Clients**, choose **Create client** → **Web application**.
6. Under **Authorised JavaScript origins**, add `https://friendsoflakemattoon.org`.
7. Copy the **Client ID**. It ends in `.apps.googleusercontent.com` and is not
   a secret.

### 2. GitHub token for the backend

At <https://github.com/settings/personal-access-tokens/new>:

- **Name:** `Lake Watch publisher (Apps Script)`
- **Expiration:** 1 year. Put a reminder in your calendar.
- **Repository access:** only `friendsoflakemattoon.github.io`
- **Permissions:** Contents: Read and write, and Pull requests: Read and write

Then delete the token you made for the old sign-in page.

### 3. The Apps Script

1. At <https://script.google.com/>, choose **New project** and name it
   `Lake Watch publisher`.
2. Replace the contents of `Code.gs` with this folder's `Code.gs`.
3. Open **Project Settings** (the gear icon), go to **Script Properties**, and add:

| Property | Value |
|---|---|
| `GITHUB_TOKEN` | the token from step 2 |
| `GOOGLE_CLIENT_ID` | the client ID from step 1 |
| `PUBLISHER_EMAILS` | your Gmail, plus anyone whose stories should go live straight away (comma-separated) |
| `EDITOR_EMAILS` | *(optional)* people whose stories should wait as a pull request for your approval |

4. Choose **Deploy → New deployment**, then the gear icon → **Web app**:
   - **Execute as:** Me
   - **Who has access:** Anyone. This is required, because the browser calls the
     script without Google cookies. The ID token check is the lock.
5. Authorise it when asked. It needs "connect to an external service", which is
   GitHub and Google's token checker.
6. Copy the **Web app URL**. It ends in `/exec`.

### 4. Point the page at it

In `lake-admin.html`, set `GOOGLE_CLIENT_ID` and `BACKEND_URL` near the top
of the script. Both are public by design.

## Changing who can publish

Edit `PUBLISHER_EMAILS` or `EDITOR_EMAILS` in Script Properties. The change
takes effect on that person's next click, and nothing needs redeploying.

## Updating the script

After you paste a new `Code.gs`, choose **Deploy → Manage deployments → Edit →
Version: New version**. This keeps the same URL. **New deployment** would create
a new URL instead.

## Who did what

Pull requests say only "a signed-in publisher", because the repository is public.
The email behind each change is in the script's **Executions** log, which only
you can see.
