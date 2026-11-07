        categories = deduplicate_across_categories(candidates)
        # A dated Naver section can return many raw cards but fewer usable
        # Korean-language international headlines after filtering. Top up
        # only the short historical world category from same-day publisher RSS.
        if day_key == "archive" and len(categories.get("세계", [])) < 10:
            extra_world = []
            for source, feed_url in FEEDS["세계"]:
                try:
                    extra_world.extend(fetch_feed(source, feed_url, target_date))
                except Exception as exc:
                    diagnostics[f"{target_date}:세계보충:{source}"] = {"ok": False, "error": str(exc)[:180]}
            for row in extra_world:
                row["category"] = "세계"
            temp = {"세계": extra_world}
            attach_naver_links(temp)
            existing_links = {((row.get("naverLink") or row.get("link") or "").split("?", 1)[0]) for row in categories.get("세계", [])}
            existing_titles = {re.sub(r"[^가-힣a-z0-9]", "", row.get("title", "").lower()) for row in categories.get("세계", [])}
            for row in temp["세계"]:
                title_key = re.sub(r"[^가-힣a-z0-9]", "", row.get("title", "").lower())
                link = (row.get("naverLink") or "").split("?", 1)[0]
                if (link.startswith("https://") and "news.naver.com/" in link
                        and link not in existing_links and title_key and title_key not in existing_titles
                        and re.search(r"[가-힣]", row.get("title", ""))):
                    row["link"] = link
                    categories["세계"].append(row)
                    existing_links.add(link)
                    existing_titles.add(title_key)
            diagnostics[f"{target_date}:세계보충:결과"] = {"ok": True, "items": len(categories["세계"])}
        # Every story here already has a verified direct Naver News URL.
        for name in categories:
            categories[name] = choose_diverse(categories[name], 10)

        # Fetch a real article lead when the RSS snippet merely repeats the headline.
        needs_summary = []
        for name, rows in categories.items():
            for row in rows: