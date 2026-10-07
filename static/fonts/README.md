# Local UI fonts

These unmodified fonts are served by Streamlit from `app/static/fonts/`.
The analyst console does not need Google Fonts or an Internet connection to
render its Figma typography. Keep sensitive data outside the `static` directory.

| Asset | Source | License |
| --- | --- | --- |
| `InterVariable.woff2` | [Inter](https://github.com/rsms/inter/blob/master/docs/font-files/InterVariable.woff2) | `Inter-LICENSE.txt` (SIL OFL 1.1) |
| `JetBrainsMono-Variable.ttf` | [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono/blob/master/fonts/variable/JetBrainsMono%5Bwght%5D.ttf) | `JetBrainsMono-OFL.txt` (SIL OFL 1.1) |

Downloaded on 2026-10-07. SHA-256 checksums of the bundled files:

```text
693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3  InterVariable.woff2
3cfafa86e28b87184d592fef82846e8c10cb48653c62efcda34f082da225ec34  JetBrainsMono-Variable.ttf
```

Both license files include the respective copyright notices. The Docker image
copies the static assets alongside the app's theme configuration. Restart the
Streamlit server after changing the `theme.fontFaces` configuration.
